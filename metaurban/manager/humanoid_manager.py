import logging
from collections import namedtuple

import math
import cv2
import numpy as np

from metaurban.component.road_network import Road
from metaurban.manager.base_manager import BaseManager
from metaurban.policy.orca_planner import OrcaPlanner
import metaurban.policy.orca_planner_utils as orca_planner_utils
from metaurban.policy.get_planning import get_planning
from metaurban.engine.logger import get_logger
logger = get_logger()

import torch
import numpy as np
import os.path as osp
from metaurban.engine.logger import get_logger
from stable_baselines3 import PPO

from metaurban.utils.math import panda_vector
from panda3d.core import Point3, Vec2, LPoint3f
dests = [(10.0, 14.0), (63.5, -45.0), (8.0, 14.0), (10.0, 18.0), (18.0, 18.0)]
# dests = [(63.0 45.0), (8.0, 14.0), (10.0, 18.0), (18.0, 18.0)]
def rule_expert(vehicle, deterministic=False, need_obs=False):
    dest_pos = vehicle.navigation.get_checkpoints()[0]
    position = vehicle.position

    dest = panda_vector(dest_pos[0], dest_pos[1])
    vec_to_2d = dest - position
    dist_to = vec_to_2d.length()

    heading = Vec2(*vehicle.heading).signedAngleDeg(vec_to_2d) * 3

    if dist_to > 2:
        vehicle._body.setAngularMovement(heading)
        vehicle._body.setLinearMovement(LPoint3f(0 , 1, 0) * 6, True)
    else:
        vehicle._body.setLinearMovement(LPoint3f(0 , 1, 0) * 1, True)
    return None

def get_dest_heading(obj, dest_pos):
    position = obj.position

    dest = panda_vector(dest_pos[0], dest_pos[1])
    vec_to_2d = dest - position
    
    heading = Vec2(*obj.heading).signedAngleDeg(vec_to_2d)

    return heading

ckpt_path = osp.join(osp.dirname(__file__), "expert_weights.npz")
_expert_weights = None
_expert_observation = None

logger = get_logger()

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

BlockHumanoids = namedtuple("block_humanoids", "trigger_road humanoids")
class HumanoidMode:
    # Humanoids will be respawned, once they arrive at the destinations
    Respawn = "respawn"

    # Humanoids will be triggered only once
    Trigger = "trigger"

    # Hybrid, some humanoids are triggered once on map and disappear when arriving at destination, others exist all time
    Hybrid = "hybrid"


class PGBackgroundSidewalkAssetsManager(BaseManager):
    VEHICLE_GAP = 10  # m

    def __init__(self):
        """
        Control the whole traffic flow
        """
        super(PGBackgroundSidewalkAssetsManager, self).__init__()

        self._traffic_humanoids = []

        # triggered by the event. TODO(lqy) In the future, event trigger can be introduced
        self.block_triggered_humanoids = []
        self.humanoids_on_block = []

        # traffic property
        self.mode = self.engine.global_config["traffic_mode"]
        self.random_traffic = self.engine.global_config["random_traffic"]
        self.density = self.engine.global_config["traffic_density"]
        self.respawn_lanes = None

        self.sidewalks = {}
        self.crosswalks = {}
        self.walkable_regions_mask = None
        self.start_points = None
        self.end_points = None
        self.mask_delta = 2
        
        self.spawn_num = self.engine.global_config["spawn_human_num"] + \
                            self.engine.global_config["spawn_wheelchairman_num"] + \
                            self.engine.global_config['spawn_edog_num'] + self.engine.global_config['spawn_erobot_num']
        self.d_robot_num = self.engine.global_config['spawn_drobot_num']
        self.max_actor_num = self.engine.global_config["max_actor_num"]

    def reset(self):
        """
        Generate traffic on map, according to the mode and density
        :return: List of Traffic vehicles
        """
        seed = self.engine.global_random_seed + 1
        import os, random
        import numpy as np
        import torch
        random.seed(seed)
        os.environ['PYTHONHASHSEED'] = str(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        
        current_map = self.current_map
        logging.debug("load scene {}".format("Use random traffic" if self.random_traffic else ""))
        
        # update vehicle list
        self.block_triggered_humanoids = []

        # get walkable region
        self.walkable_regions_mask = self._get_walkable_regions(current_map)
        
        # get walkable region
        self.start_points, self.end_points = self.random_start_and_end_points(self.walkable_regions_mask[:, :, 0], self.spawn_num + self.d_robot_num)
        time_length, points, speed, early_stop_points = get_planning(
                    [self.start_points],
                    
                    [self.walkable_regions_mask],
                    
                    [self.end_points],
                    
                    [len(self.start_points)],
                    
                    1
            )
        self.points = iter(points[0])
        self.time_length = time_length[0]
        self.speeds = iter(speed[0])
        self.es_points = early_stop_points[0]
        # spawn humanoids
        assert self.mode == HumanoidMode.Trigger
        self._create_humanoids_once(current_map, self.spawn_num, self.max_actor_num)
        self._create_deliveryrobots_once(current_map, self.d_robot_num, offset=self.spawn_num)


    def before_step(self):
        # trigger vehicles
        engine = self.engine
        if self.mode != HumanoidMode.Respawn:
            for v in engine.agent_manager.active_agents.values():
                try:
                    ego_lane_idx = v.lane_index[:-1]
                    ego_road = Road(ego_lane_idx[0], ego_lane_idx[1])
                    # the trigger of the moving of traffic vehicles
                    if len(self.block_triggered_humanoids) > 0 and \
                            ego_road == self.block_triggered_humanoids[-1].trigger_road:
                        block_humanoids = self.block_triggered_humanoids.pop()
                        self._traffic_humanoids += list(self.get_objects(block_humanoids.humanoids).values())
                except:
                    if len(self.block_triggered_humanoids) > 0:
                        block_humanoids = self.block_triggered_humanoids.pop()
                        self._traffic_humanoids += list(self.get_objects(block_humanoids.humanoids).values())
        return dict()

    def after_step(self, *args, **kwargs):
        # todo: 1) update end points; 2) remove humanoids
        v_to_remove = []


        if len(self._traffic_humanoids) == 0:
            return
        
        try:
            positions, speeds = next(self.points), next(self.speeds)
        except:
            import copy
            self.start_points = copy.deepcopy(self.end_points)
            _, self.end_points = self.random_start_and_end_points(self.walkable_regions_mask[:, :, 0], self.spawn_num + self.d_robot_num)
            time_length, points, speed, early_stop_points = get_planning(
                        [self.start_points],
                        
                        [self.walkable_regions_mask],
                        
                        [self.end_points],
                        
                        [len(self.start_points)],
                        
                        1
                )
            self.points = iter(points[0])
            self.time_length = time_length[0]
            self.speeds = iter(speed[0])
            self.es_points = early_stop_points[0]
            positions, speeds = next(self.points), next(self.speeds)
        for v, pos, speed in zip(self._traffic_humanoids, positions, speeds):
            pos = self._to_block_coordinate(pos)  #### 
            prev_pos = v.position
            heading = get_dest_heading(v, pos)
            speed = speed / self.engine.global_config["physics_world_step_size"]
            from metaurban.component.agents.pedestrian.base_pedestrian import BasePedestrian
            if isinstance(v, BasePedestrian):
                if v.render:
                    v.set_anim_by_speed(speed)
            v.set_position(pos)
            try:
                v._body.setAngularMovement(heading * 3) #### TODO #larger heading
            except:
                heading = np.arctan2(pos[1] - prev_pos[1], 
                                    pos[0] - prev_pos[0])
                v.set_heading_theta(heading) ### 2. change heading directly

        return dict()

    def _get_walkable_regions(self, current_map):
        self.crosswalks = current_map.crosswalks
        self.sidewalks = current_map.sidewalks
        self.sidewalks_near_road = current_map.sidewalks_near_road
        self.sidewalks_farfrom_road = current_map.sidewalks_farfrom_road
        self.sidewalks_near_road_buffer = current_map.sidewalks_near_road_buffer
        self.sidewalks_farfrom_road_buffer = current_map.sidewalks_farfrom_road_buffer
        self.valid_region = current_map.valid_region

        polygons = []
        for sidewalk in self.sidewalks.keys():
            # if "SDW_I_" in sidewalk: continue
            polygon = self.sidewalks[sidewalk]['polygon']
            polygons += polygon
        for crosswalk in self.crosswalks.keys():
            # if "CRS_I_" in crosswalk: continue
            polygon = self.crosswalks[crosswalk]['polygon']
            polygons += polygon
            
        for sidewalk in self.sidewalks_near_road_buffer.keys():
            polygon = self.sidewalks_near_road_buffer[sidewalk]['polygon']
            polygons += polygon
        for sidewalk in self.sidewalks_near_road.keys():
            polygon = self.sidewalks_near_road[sidewalk]['polygon']
            polygons += polygon
        for sidewalk in self.sidewalks_farfrom_road.keys():
            polygon = self.sidewalks_farfrom_road[sidewalk]['polygon']
            polygons += polygon
        for sidewalk in self.sidewalks_farfrom_road_buffer.keys():
            polygon = self.sidewalks_farfrom_road_buffer[sidewalk]['polygon']
            polygons += polygon
        for sidewalk in self.valid_region.keys():
            polygon = self.valid_region[sidewalk]['polygon']
            polygons += polygon

        polygon_array = np.array(polygons)
        min_x = np.min(polygon_array[:, 0])
        max_x = np.max(polygon_array[:, 0])
        min_y = np.min(polygon_array[:, 1])
        max_y = np.max(polygon_array[:, 1])

        rows = math.ceil(max_y - min_y) + 2*self.mask_delta
        columns = math.ceil(max_x - min_x) + 2*self.mask_delta

        self.mask_translate = np.array([-min_x+self.mask_delta, -min_y+self.mask_delta])
        if hasattr(self.engine, 'walkable_regions_mask'):
            return self.engine.walkable_regions_mask
        
        walkable_regions_mask = np.zeros((rows, columns, 3), np.uint8)

        for sidewalk in self.sidewalks.keys():
            # if "SDW_I_" in sidewalk: continue
            polygon_array = np.array(self.sidewalks[sidewalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])

        for crosswalk in self.crosswalks.keys():
            # if "SDW_I_" in sidewalk: continue
            polygon_array = np.array(self.crosswalks[crosswalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])
            
        for sidewalk in self.sidewalks_near_road_buffer.keys():
            polygon_array = np.array(self.sidewalks_near_road_buffer[sidewalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])
        for sidewalk in self.sidewalks_near_road.keys():
            polygon_array = np.array(self.sidewalks_near_road[sidewalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])
        for sidewalk in self.sidewalks_farfrom_road.keys():
            polygon_array = np.array(self.sidewalks_farfrom_road[sidewalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])
        for sidewalk in self.sidewalks_farfrom_road_buffer.keys():
            polygon_array = np.array(self.sidewalks_farfrom_road_buffer[sidewalk]['polygon'])
            polygon_array += self.mask_translate
            polygon_array = np.floor(polygon_array).astype(int)
            polygon_array = polygon_array.reshape((-1, 1, 2))
            cv2.fillPoly(walkable_regions_mask, [polygon_array], [255, 255, 255])
        walkable_regions_mask = cv2.flip(walkable_regions_mask, 0)   ### flip for orca   ###### 
        # cv2.imwrite('1111.png', walkable_regions_mask)

        return walkable_regions_mask

    def _random_points_new(self, map_mask, num, min_dis=5):
        import matplotlib.pyplot as plt
        from scipy.signal import convolve2d
        import random
        from skimage import measure
        h, _ = map_mask.shape
        import metaurban.policy.orca_planner_utils as orca_planner_utils
        mylist, h, w = orca_planner_utils.mask_to_2d_list(map_mask)
        contours = measure.find_contours(mylist, 0.5, positive_orientation='high')
        flipped_contours = []
        for contour in contours:
            contour = orca_planner_utils.find_tuning_point(contour, h)
            flipped_contours.append(contour)
        int_points = []
        for p in flipped_contours:
            for m in p:
                int_points.append((int(m[1]), int(m[0])))
        def find_walkable_area(map_mask):
            kernel = np.array([[1,1,1],[1,0,1],[1,1,1]], dtype=np.uint8)
            conv_result= convolve2d(map_mask/255, kernel, mode='same')
            ct_pts = np.where(conv_result==8) #8, 24
            ct_pts = list(zip(ct_pts[1], ct_pts[0]))
            ct_pts = [c for c in ct_pts if c not in int_points]
            return ct_pts
        selected_pts = []
        walkable_pts = find_walkable_area(map_mask)
        random.shuffle(walkable_pts)
        if len(walkable_pts) < num: raise ValueError(" Walkable points are less than spawn number! ")
        try_time = 0
        while len(selected_pts) < num:
            if try_time > 10000: raise ValueError("Try too many time to get valid humanoid points!")
            cur_pt = random.choice(walkable_pts)
            if all(math.dist(cur_pt, selected_pt) >= min_dis for selected_pt in selected_pts): 
                selected_pts.append(cur_pt)
            try_time+=1
        selected_pts = [(x[0], h - 1 - x[1]) for x in selected_pts]
        return selected_pts
    
    def _random_points(self, map_mask, num):
        def in_walkable_area(x):
            positions = [(0, 0), (3, 3), (-3, -3), (-3, 3), (3, -3)]
            try:
                return all(map_mask[x[1] + pos[0], x[0] + pos[1]] == 255 for pos in positions)
            except IndexError: return False
            
        def is_close_to_points(x, pts, filter_rad=5):
            return any(math.dist(x,pt) < filter_rad for pt in pts)

        pts = []
        h, w = map_mask.shape
        
        while len(pts) < num:
            pt = (np.random.randint(0, w - 1), np.random.randint(0, h - 1))
            if not in_walkable_area(pt): continue
            if len(pts) > 0 and is_close_to_points(pt, pts): continue
            pts.append(pt)
        pts = [(x[0], h - 1 - x[1]) for x in pts]

        return pts

    def random_start_and_end_points(self, map_mask, num):
        ### cv2.erode
        starts = self._random_points_new(map_mask, num)
        goals = self._random_points_new(map_mask, num)

        return starts, goals
    
    def _create_humanoids_once(self, map, spawn_num, max_actor_num) -> None:
        humanoid_num = 0
        heading = 0
        humanoids_on_block = []
        static_humanoids_on_block = []
        block = map.blocks[1]
        # simply give head and position
        selected_humanoid_configs = []
        for i in range(spawn_num):
            spawn_point = self._to_block_coordinate(self.start_points[i]) 
            random_humanoid_config = {"spawn_position_heading": [spawn_point, heading]}
            selected_humanoid_configs.append(random_humanoid_config)

        agent_types = [self.random_humanoid_type] * self.engine.global_config["spawn_human_num"] + \
                        [self.random_wheelchair_type] * self.engine.global_config["spawn_wheelchairman_num"] + \
                        [self.random_edog_type] * self.engine.global_config['spawn_edog_num'] + \
                        [self.random_erobot_type] * self.engine.global_config['spawn_erobot_num'] 
        
        for kk, v_config in enumerate(selected_humanoid_configs):
            humanoid_type = agent_types[kk]

            v_config.update(self.engine.global_config["traffic_vehicle_config"])
            random_v = self.spawn_object(humanoid_type, vehicle_config=v_config)
            humanoids_on_block.append(random_v.name)
        trigger_road = block.pre_block_socket.positive_road
        block_humanoids = BlockHumanoids(trigger_road=trigger_road, humanoids=humanoids_on_block)
        self.block_triggered_humanoids.append(block_humanoids) 
        humanoid_num += len(humanoids_on_block)
        self.block_triggered_humanoids.reverse()
        
    def _create_deliveryrobots_once(self, map, spawn_num, offset) -> None:
        deliveryrobot_num = 0
        heading = 0
        deliveryrobots_on_block = []
        block = map.blocks[1]
        # simply give head and position
        selected_humanoid_configs = []
        for i in range(offset, offset + spawn_num):
            spawn_point = self._to_block_coordinate(self.start_points[i]) 
            random_deliveryrobot_type = {"spawn_position_heading": [spawn_point, heading]}
            selected_humanoid_configs.append(random_deliveryrobot_type)
        
        for v_config in selected_humanoid_configs:
            deliveryrobot_type = self.random_deliveryrobot_type
            v_config.update(self.engine.global_config["traffic_vehicle_config"])
            random_v = self.spawn_object(deliveryrobot_type, vehicle_config=v_config)
            deliveryrobots_on_block.append(random_v.name)

        trigger_road = block.pre_block_socket.positive_road
        block_humanoids = BlockHumanoids(trigger_road=trigger_road, humanoids=deliveryrobots_on_block)
        self.block_triggered_humanoids.append(block_humanoids)      ### BlockHumanoids
        deliveryrobot_num += len(deliveryrobots_on_block)

        self.block_triggered_humanoids.reverse()
    
    @property
    def random_humanoid_type(self) -> object:
        from metaurban.component.agents.pedestrian.pedestrian_type import SimplePedestrian
        return SimplePedestrian #(max_actor_num=10)

    @property
    def random_erobot_type(self) -> object:
        from metaurban.component.agents.pedestrian.pedestrian_type import ErobotPedestrian
        return ErobotPedestrian
    @property
    def random_wheelchair_type(self) -> object:
        from metaurban.component.agents.pedestrian.pedestrian_type import WheelchairPedestrian
        return  WheelchairPedestrian
    @property
    def random_edog_type(self) -> object:
        from metaurban.component.agents.pedestrian.pedestrian_type import EdogPedestrian
        return  EdogPedestrian

    @property
    def random_static_humanoid_type(self) -> object:
        from metaurban.component.agents.pedestrian.pedestrian_type import StaticPedestrian
        return StaticPedestrian
    
    @property
    def random_robotdog_type(self) -> object:
        from metaurban.component.robotdog.robotdog_type import DefaultVehicle

        return DefaultVehicle
    
    @property
    def random_deliveryrobot_type(self) -> object:
        from metaurban.component.delivery_robot.deliveryrobot_type import DefaultVehicle

        return DefaultVehicle

    @property
    def current_map(self) -> object:
        return self.engine.map_manager.current_map

    def _to_block_coordinate(self, point_in_mask: object) -> object:
        point_in_block = point_in_mask - self.mask_translate
        return point_in_block
