from typing import List
import datetime
import os
import random
from pathlib import Path
import numpy as np
import torch
from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import AllChem, MACCSkeys

# Disable RDKit logs
RDLogger.DisableLog("rdApp.*")


def set_seed(seed_value):
    random.seed(seed_value)
    # Set seed for NumPy
    np.random.seed(seed_value)
    # Set seed for PyTorch
    torch.manual_seed(seed_value)


def get_short_name_for_ckpt_path(chpt_path: str, hash_len: int = 6):
    get_short_name_for_ckpt_path = Path(chpt_path)
    return (
        get_short_name_for_ckpt_path.parent.name[:hash_len]
        + "-"
        + get_short_name_for_ckpt_path.name.split("-")[-1]
    )


def get_morgan_fingerprint(mol):
    return AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)


def get_maccs_fingerprint(mol):
    return MACCSkeys.GenMACCSKeys(mol)


def tanimoto_dist_func(fing1, fing2, fingerprint: str = "morgan"):
    return DataStructs.TanimotoSimilarity(
        fing1 if fingerprint == "morgan" else fing1,
        fing2 if fingerprint == "morgan" else fing2,
    )


def generate_random_number(lower, upper):
    return lower + random.random() * (upper - lower)


def canonicalize(smiles):
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles), canonical=True)
    # return Chem.MolToSmiles(Chem.MolFromSmiles(smiles), kekuleSmiles=True)


class MoleculeEntry:
    def __init__(self, smiles, score=0, **kwargs):
        self.smiles = smiles
        self.score = score
        if smiles:
            self.smiles = canonicalize(smiles)
            self.mol = Chem.MolFromSmiles(smiles)
            self.fingerprint = get_morgan_fingerprint(self.mol)
        self.add_props = kwargs

    def __eq__(self, other):
        return self.smiles == other.smiles

    def __lt__(self, other):
        if self.score == other.score:
            return self.smiles < other.smiles
        return self.score < other.score

    def __str__(self):
        return (
            f"smiles: {self.smiles}, "
            f"score: {round(self.score, 4) if self.score != None else 'none'}"
        )

    def __repr__(self):
        return str(self)


class Pool:
    def __init__(self, size, validation_perc: float):
        self.size = size
        self.optim_entries: List[OptimEntry] = []
        self.num_validation_entries = int(size * validation_perc)

    # def random_dump(self, num):
    #     for _ in range(num):
    #         rand_ind = random.randint(0, num - 1)
    #         self.molecule_entries.pop(rand_ind)
    #     print(f"Dump {num} random elements from pool, num pool mols {len(self)}")

    def add(self, entries: List, diversity_score=1.0):
        assert type(entries) == list
        self.optim_entries.extend(entries)
        self.optim_entries.sort(key=lambda x: x.last_entry, reverse=True)

        # remove doublicates
        new_optim_entries = []
        for entry in self.optim_entries:
            insert = True
            for e in new_optim_entries:
                if (
                    entry == e
                    or tanimoto_dist_func(
                        entry.last_entry.fingerprint, e.last_entry.fingerprint
                    )
                    > diversity_score
                ):
                    insert = False
                    break
            if insert:
                new_optim_entries.append(entry)

        self.optim_entries = new_optim_entries[: min(len(new_optim_entries), self.size)]
        curr_num_validation_entries = sum([entry.entry_status == EntryStatus.valid for entry in self.optim_entries])

        i = 0
        while curr_num_validation_entries < self.num_validation_entries:
            if self.optim_entries[i].entry_status == EntryStatus.none:
                self.optim_entries[i].entry_status = EntryStatus.valid
                curr_num_validation_entries += 1
            i += 1
        
        for j in range(i, len(self.optim_entries)):
            if self.optim_entries[j].entry_status == EntryStatus.none:
                self.optim_entries[j].entry_status = EntryStatus.train
        
        curr_num_validation_entries = sum([entry.entry_status == EntryStatus.valid for entry in self.optim_entries])
        assert curr_num_validation_entries == min(len(self.optim_entries), self.num_validation_entries)

    def get_train_valid_entries(self):
        train_entries = []
        valid_entries = []
        for entry in self.optim_entries:
            if entry.entry_status == EntryStatus.train:
                train_entries.append(entry)
            elif entry.entry_status == EntryStatus.valid:
                valid_entries.append(entry)
            else:
                raise Exception(f"EntryStatus of an entry in pool cannot be {entry.entry_status}.")
        assert min(len(self.optim_entries), self.num_validation_entries) == len(valid_entries)
        return train_entries, valid_entries

    def random_subset(self, subset_size):
        rand_inds = np.random.permutation(min(len(self.optim_entries), subset_size))
        return [self.optim_entries[i] for i in rand_inds]

    def __len__(self):
        return len(self.optim_entries)


def make_output_files_base(input_path, results_dir, run_name, config):
    formatted_date_time = datetime.datetime.now().strftime("%Y-%m-%d")
    base = os.path.join(input_path, run_name, formatted_date_time)
    os.makedirs(base, exist_ok=True)
    v = 0
    strategy = "+".join(config["strategy"])
    while os.path.exists(os.path.join(base, f"{strategy}-{v}")):
        v += 1
    output_dir = os.path.join(base, f"{strategy}-{v}")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def create_prompt_with_similars(mol_entry: MoleculeEntry, sim_range=None):
    prompt = ""
    for sim_mol_entry in mol_entry.add_props["similar_mol_entries"]:
        if sim_range:
            prompt += f"[SIMILAR]{sim_mol_entry.smiles} {generate_random_number(sim_range[0], sim_range[1]):.2f}[/SIMILAR]"  # noqa
        else:
            prompt += f"[SIMILAR]{sim_mol_entry.smiles} {tanimoto_dist_func(sim_mol_entry.fingerprint, mol_entry.fingerprint):.2f}[/SIMILAR]"  # noqa
    return prompt


class EntryStatus:
    none = 0
    train = 1
    valid = 2


class OptimEntry:
    def __init__(self, last_entry, mol_entries):
        self.last_entry: MoleculeEntry = last_entry
        self.mol_entries: List[MoleculeEntry] = mol_entries
        self.entry_status: EntryStatus = EntryStatus.none

    def to_prompt(self, is_generation: bool, include_oracle_score: bool, config):
        prompt = ""
        prompt = config["eos_token"]
        for mol_entry in self.mol_entries:
            # prompt += config["eos_token"]
            if "default" in config["strategy"]:
                prompt += create_prompt_with_similars(mol_entry=mol_entry)
            elif "rej-sample-v2" in config["strategy"]:
                prompt += create_prompt_with_similars(mol_entry=mol_entry)
                if include_oracle_score:
                    prompt += f"[PROPERTY]oracle_score {mol_entry.score:.2f}[/PROPERTY]"
            else:
                raise Exception(f"Strategy {config['strategy']} not known.")
            prompt += f"[START_SMILES]{mol_entry.smiles}[END_SMILES]"

        assert self.last_entry
        # prompt += config["eos_token"]
        if is_generation:
            prompt_with_similars = create_prompt_with_similars(
                self.last_entry, sim_range=config["sim_range"]
            )
        else:
            prompt_with_similars = create_prompt_with_similars(self.last_entry)

        if "default" in config["strategy"]:
            prompt += prompt_with_similars
        elif "rej-sample-v2" in config["strategy"]:
            prompt += prompt_with_similars
            if is_generation:
                oracle_scores_of_mols_in_prompt = [e.score for e in self.mol_entries]
                q_0_9 = (
                    np.quantile(oracle_scores_of_mols_in_prompt, 0.9)
                    if oracle_scores_of_mols_in_prompt
                    else 0
                )
                desired_oracle_score = generate_random_number(
                    q_0_9, config["max_possible_oracle_score"]
                )
                oracle_score = desired_oracle_score
            else:
                oracle_score = self.last_entry.score
            if include_oracle_score:
                prompt += f"[PROPERTY]oracle_score {oracle_score:.2f}[/PROPERTY]"
        else:
            raise Exception(f"Strategy {config['strategy']} not known.")

        if is_generation:
            prompt += "[START_SMILES]"
        else:
            prompt += f"[START_SMILES]{self.last_entry.smiles}[END_SMILES]"

        return prompt

    def contains_entry(self, mol_entry: MoleculeEntry):
        for entry in self.mol_entries:
            if mol_entry == entry:
                return True
        return False
