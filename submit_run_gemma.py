import sys
from contextlib import contextmanager
from datetime import datetime
import submitit

use_accelerate = True
rsync_enabled = False
executor_name = "local"  # options are ["slurm", "local"]
root_path = ""
num_gpus = 2
model_name = "gemma"
model_size = "2b"
train_type = "pretrain"
train_name = "_".join([model_name, model_size, train_type])
job_name = "gemma_test_dynamic_ga"

slurm_params = {
    "slurm_job_name": job_name,
    "timeout_min": 3600,
    "nodes": 1,
    "tasks_per_node": 1,
    "gpus_per_node": num_gpus,
    "cpus_per_task": num_gpus * 8,
    "mem_gb": num_gpus * 40.0 + 20.0,
    "stderr_to_stdout": True,
}

accelerate_config = {"num_processes": num_gpus}

env_variables = {
    "HF_HOME": "~",
    "HF_TOKEN": "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",
    "TOKENIZERS_PARALLELISM": "false",
    "CUDA_VISIBLE_DEVICES": "0, 1, 2, 3, 4, 5, 6, 7",
}

cli_arguments = {
    "train_type": train_type,
    "from_pretrained": "google/gemma-2b",
    "model_config": train_name,
    "dir_data_types": "computed",
    "training_data_dirs": "/nfs/ap/mnt/sxtn/rdkit_computed_rel+form/train_rdkit_computed_rel+form",
    "valid_data_dir": "/nfs/ap/mnt/sxtn/rdkit_computed_rel+form/valid_rdkit_computed_rel+form",
    "max_steps": 22000,
    # "num_train_epochs": 2,
    "eval_steps": 4,
    "save_steps": 2,
    "train_batch_size": 1,
    # "valid_batch_size":,s
    "dataloader_num_workers": 1,
    "experiment_name": job_name,
    "checkpoints_root_dir": "/nfs/dgx/raid/chem/checkpoints/",
    "flash_attn": True,
    "track": True,
    "track_dir": "/nfs/dgx/raid/chem/aim/",
    # "profile":,
    # "profile_dir":,
    "gradient_accumulation_steps": 1,
    # "gradient_checkpointing": False,
    # "evaluate_only":,
    # "check_reproducability":,
}


def get_command(use_accelerate):
    python_executable = sys.executable
    command = [python_executable]
    if use_accelerate:
        accelerate_path = f"chemlactica/config/{model_name}_accelerate_config.yaml"
        command.extend(
            f"-m accelerate.commands.launch --config_file {accelerate_path}".split(" ")
        )
        for k, v in accelerate_config.items():
            command.append(f"--{k}={v}")
    command.append("chemlactica/train.py")
    for x, y in cli_arguments.items():
        if isinstance(y, bool):
            if y:
                command.append(f"--{x}")
        else:
            command.append(f"--{x}={y}")

    print(f'command being executed: {" ".join(command)}')
    return command


@contextmanager
def conditional_context_manager(rsync_enabled, repo_path):
    if rsync_enabled:
        with submitit.helpers.RsyncSnapshot(repo_path) as cm:
            yield cm
    else:
        yield None


def get_executor(executor_name, logs_path):
    if executor_name == "slurm":
        executor = submitit.AutoExecutor(folder=logs_path)
    elif executor_name == "local":
        executor = submitit.local.local.LocalExecutor(folder=logs_path)
    return executor


if __name__ == "__main__":
    train_name = "_".join([model_name, model_size, train_type])
    logs_path = "submitit_logs/%j"
    logs_path = "/nfs/dgx/raid/chem/" + logs_path if rsync_enabled else logs_path
    repo_path = (
        "/nfs/dgx/raid/chem/rsyncsnapshots/"
        f"{train_name}-{datetime.now().strftime('%Y-%m-%d-%H:%M')}"
    )

    with conditional_context_manager(rsync_enabled, repo_path):
        command = get_command(use_accelerate)
        executor = get_executor(executor_name, logs_path)
        executor.update_parameters(**slurm_params)
        print("train_name: ", train_name)
        print("logs_path: ", logs_path)
        print("repo path: ", repo_path)
        function = submitit.helpers.CommandFunction(command, env=env_variables)
        job = executor.submit(function)
