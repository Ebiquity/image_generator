#!/bin/bash
#!/bin/bash
#SBATCH --job-name=image_generator
#SBATCH --output=slurm.out
#SBATCH --error=slurm.err
#SBATCH --partition=gpu
#SBATCH --nodelist=g09
#SBATCH --gres=gpu:1
#SBATCH --qos=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=30G
#SBATCH --time=36:00:00

srun python main_2.py
