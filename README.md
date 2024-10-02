# MetaUrban: An Embodied AI Simulation Platform for Urban Micromobility

**[Anonymous Code for ICLR Review]**

*TL;DR: MetaUrban is a compositional simulation platform for AI-driven urban micromobility research. It will be open-source to enable more research opportunities for the community, and foster generalizable and safe embodied AI and micromobility in cities.*

## Abstract
Public urban spaces like streetscapes and plazas serve residents and accommodate social life in all its vibrant variations. Recent advances in Robotics and Embodied AI make public urban spaces no longer exclusive to humans. Food delivery bots and electric wheelchairs have started sharing sidewalks with pedestrians, while robot dogs and humanoids have recently emerged in the street. **Micromobility** enabled by AI for short-distance travel in public urban spaces plays a crucial component in the future transportation system. Ensuring the generalizability and safety of AI models maneuvering mobile machines is essential.

In this work, we present **MetaUrban**, a compositional simulation platform for the AI-driven urban micromobility research.
MetaUrban can construct an infinite number of interactive urban scenes from compositional elements, covering a vast array of ground plans, object placements, pedestrians, vulnerable road users, and other mobile agents' appearances and dynamics. We design point navigation and social navigation tasks as the pilot study using MetaUrban for urban micromobility research and establish various baselines of Reinforcement Learning and Imitation Learning. We conduct extensive evaluation across mobile machines, demonstrating that heterogeneous mechanical structures significantly influence the learning and execution of AI policies. We perform a thorough ablation study, showing that the compositional nature of the simulated environments can substantially improve the generalizability and safety of the trained mobile agents.

## 🛠 Quick Start
### Hardware Recommendation
We have tested the project on Linux, Windows and WSL2. For MacOS, a few control and display issues remain to be solved. We strongly recommend using Ubuntu with Nvidia GPUs (with at least **8GB CPU** and **3GB GPU** Memory) to run the simulator. The performance benchmarks are conducted on different machines with **Nvidia RTX-3090, RTX-4080, RTX-4090, RTX-A5000 and Tesla V100**. It's normal that running PointNavigation env by `metaurban/examples/drive_in_static_env.py` with **~60FPS** and **~2GB** GPU Memory. 

### Installation

```bash
git clone -b main https://github.com/metaurban-iclr-2025/MetaUrban.git
cd metaurban
pip install -e .
```

install ORCA algorithm for trajectory generation

```bash
pip install scikit-image, pyyaml
conda install pybind11 -c conda-forge
cd metaurban/orca_algo && rm -rf build
bash compile.sh && cd ../..
```

install stable-baselines3 for RL training

```bash
pip install stable_baselines3, tensorboard, wandb
```

assets will be downloaded automatically when first running the script 

`python metaurban/examples/drive_in_static_env.py`

if not, please download assets via the link:

"https://drive.google.com/file/d/16gIDeqplgV78CrGRX1LjlhvDAtLkr2VK/view?usp=sharing" for object assets

"https://drive.google.com/file/d/1Dwhmdkp6SRmCFu6gSzpqqqpo1RE74VvI/view?usp=sharing" for pedestrian assets

and organize the folder as:

```
-metaurban
  -metaurban
      -assets
      -assets_pedestrian
      -base_class
      -...
```

## 🏃‍♂️ Simulation Environment Roam
We provide examples to demonstrate features and basic usages of metaurban after the local installation.

### Point Navigation Environment

In point navigation environment, there will be only static objects besides the ego agent in the scenario.

Run the following command to launch a simple scenario with manual control. Press `W,S,A,D` to control the delivery robot. 


```bash
python -m metaurban.examples.drive_in_static_env
```

Press key ```R``` for loading a new scenario. If there is no response when you press `W,S,A,D`, press `T` to enable manual control.

### Social Navigation Environment
In social navigation environment, there will be vehicles, pedestrians and some other agents in the scenario.

Run the following command to launch a simple scenario with manual control. Press `W,S,A,D` to control the delivery robot. 

```bash
python -m metaurban.examples.drive_in_dynamic_env
```

## 🚀 Model Training

We provide scripts for RL and IL related research, based on **stable_baselines3**.

### Reinforcement Learning
#### Training
```bash
python RL/PointNav/train_ppo.py
```
for PPO training in PointNav Env. You can change the parameters in the file.

```bash
python RL/SocialNav/train_ppo.py
```
for PPO training in Social Env. You can change the parameters in the file.

### Imitation Learning
To train the neural network by IL approaches, you should collect data at first. Here we provide scripts for collecting data by human control.
#### Data Collection
```bash
python scripts/collect_data_in_custom_env.py
```

## 📖 MetaUrban-12K Dataset Generation
We provide a subset of seeds with selected ORCA reference trajectory for ego agent. You can run the command as below to generate a scenario from these seeds

```bash
python -m metaurban.scripts.generate_static_scenario
```

for PointNav environment 

```bash
python -m metaurban.scripts.generate_dynamic_scenario
```

for SocialNav environment 

#### The file `valid_seed.pkl` in the root folder is a subset of `MetaUrban-12K` Dataset. It's notable that currently provided version do not match the version we use since complete asset library is not provided yet, there may still be some unreasonable reference trajectories in the scenario.
