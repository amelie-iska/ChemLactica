import os
import time
import hashlib

from aim.hugging_face import AimCallback
import torch.distributed as dist
from transformers.trainer_callback import TrainerCallback
from transformers import AutoModel
import torch


def calc_hash_for_binary_file(path):
    with open(path, "rb") as _file:
        file_content = _file.read()
        hex_hash = hashlib.md5(file_content).hexdigest()
        return hex_hash


class CustomAimCallback(AimCallback):
    def __init__(self, checkpoints_dict_name, model, blocksize, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._checkpoints_dict_name = checkpoints_dict_name
        self.model = model
        self.setup()
        self.embedding_norm_1 = 0
        self.embedding_norm_2 = 0
        self.activations_norm = 0
        self._run[self._checkpoints_dict_name] = {}
        self.blocksize = blocksize
        self.start_time = None

    def on_save(self, args, state, control=None, **kwargs):
        checkpoint_dir = os.path.join(
            args.output_dir, f"checkpoint-{state.global_step}"
        )
        checkpoints_dict = self._run[self._checkpoints_dict_name]
        checkpoints_dict[state.global_step] = {}
        for file_name in os.listdir(checkpoint_dir):
            file_path = os.path.join(checkpoint_dir, file_name)
            if os.path.isfile(file_path):
                checkpoints_dict[state.global_step][
                    file_name
                ] = calc_hash_for_binary_file(file_path)
        self._run[self._checkpoints_dict_name] = checkpoints_dict

    # def on_step_begin(self, args, state, control, **kwargs):
    #     self.start_time = time.time()

    # def on_step_end(self, args, state, control, **kwargs):
    # Get batch size (first dimension of inputs)
    # self.embedding_norm_1 = torch.linalg.norm(
    #     self.model.get_input_embeddings().weight, ord=1
    # )
    # self.embedding_norm_2 = torch.linalg.norm(
    #     self.model.get_input_embeddings().weight, ord=2
    # )
    # self.embedding_norm_1, self.embedding_norm_2 = (
    #     0,
    #     0,
    # )  # embedding norm should be modified to work with fsdp wrapped model

    # self.experiment.track(self.embedding_norm_1, name="embedding l1 norm")
    # self.experiment.track(self.embedding_norm_2, name="embedding l2 norm")
    # pass


class WPSCounterCallback(TrainerCallback):
    def __init__(self, block_size, aim_run=None):
        self._aim_run = aim_run
        self._block_size = block_size
        self._start_time = None

    def on_step_begin(self, args, state, control, model, **kwargs):
        if dist.get_rank() == 0 and self._aim_run is not None:
            if self._start_time is not None:
                batch_size = args.per_device_train_batch_size
                # Calculate tokens in batch
                num_words = batch_size * self._block_size * args.world_size
                # Calculate time taken for this step
                elapsed_time = time.time() - self._start_time
                # Calculate words per second
                words_per_second = num_words / elapsed_time
                self._aim_run.track(words_per_second, name="words per second")

            self._start_time = time.time()


class ProfCallback(TrainerCallback):
    def __init__(self, prof):
        self.prof = prof

    def on_step_end(self, args, state, control, **kwargs):
        self.prof.step()


class ReproducabilityCallback(TrainerCallback):
    def on_save(self, args, state, control, model, **kwargs):
        checkpoint_dir = os.path.join(
            args.output_dir, f"checkpoint-{state.global_step}"
        )
        saved_model = AutoModel.from_pretrained(checkpoint_dir)
        _input = torch.randn(4, 2048, dtype=torch.float16)
        out = saved_model(_input)
        saved_out = model(_input)

        is_repr = torch.allclose(out, saved_out, atol=1e-5)
        if is_repr:
            print(f"Model at step {state.global_step} is reproducable.")
        else:
            print(f"Model at step {state.global_step} is not reproducable.")
