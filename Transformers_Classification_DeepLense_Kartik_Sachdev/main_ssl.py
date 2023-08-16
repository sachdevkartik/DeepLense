from __future__ import print_function
import os
import time

import torch.nn as nn
import torch.optim as optim
from torchinfo import summary
import torchvision
from typing import *
from utils.util import *
from utils.inference import InferenceSSL
from argparse import ArgumentParser
from config.data_config import DATASET
from torch.utils.data import DataLoader
from torch.utils.data import DataLoader
from torch.utils.data import DataLoader, random_split
from models.cnn_zoo import CustomResNet
from models.byol import BYOL

import json
import yaml
from utils.dataset import DefaultDatasetSetupSSL
from utils.losses.contrastive_loss import (
    ContrastiveLossEuclidean,
    ContrastiveLossEmbedding,
    SimCLR_Loss,
    NegativeCosineSimilarity
)
from utils.train import (
    train_simplistic,
    train_byol,
    train_contrastive_pair,
    train_contrastive_with_labels,
    train_contrastive,
)


parser = ArgumentParser()
parser.add_argument(
    "--dataset_name",
    metavar="Model_X",
    type=str,
    default="Model_Test",
    choices=["Model_I", "Model_II", "Model_III", "Model_IV", "Model_Test"],
    help="dataset type for DeepLense project",
)
parser.add_argument(
    "--save", metavar="XXX/YYY", type=str, default="data", help="destination of dataset"
)

parser.add_argument(
    "--num_workers", metavar="1", type=int, default=1, help="number of workers"
)

parser.add_argument(
    "--train_config_path",
    metavar="XXX/YYY",
    type=str,
    help="path to transformer config",
)

parser.add_argument("--cuda", action="store_true", help="whether to use cuda")
parser.add_argument(
    "--no-cuda", dest="cuda", action="store_false", help="not to use cuda"
)
parser.set_defaults(cuda=True)

args = parser.parse_args()

"""
TODO:
1. Resnet baselines: Test on Model I, II, III
2. Transformer baselines: CVT/best transformer model
"""


def main():
    dataset_name = args.dataset_name
    dataset_dir = args.save
    use_cuda = args.cuda
    num_workers = args.num_workers
    train_config_path = args.train_config_path

    classes = DATASET[f"{dataset_name}"]["classes"]
    num_classes = len(classes)

    # Open the YAML file and load its contents
    with open(train_config_path, "r") as file:
        train_config = yaml.safe_load(file)

    ########################## boilerplate ##########################

    # Set ssl config
    device = get_device(use_cuda=use_cuda, cuda_idx=0)
    print(device)

    # Set hyperparameters
    batch_size = train_config["batch_size"]
    epochs_pretrained = train_config["pretrained"]["num_epochs"]
    epochs_finetuned = train_config["finetuned"]["num_epochs"]

    learning_rate = train_config["optimizer_config"]["lr"]
    margin = train_config["ssl"]["margin"]
    num_channels = train_config["channels"]
    temperature = train_config["ssl"]["temperature"]
    network_type = train_config["network_type"]
    image_size = train_config["image_size"]
    optimizer_config = train_config["optimizer_config"]

    backbone = train_config["backbone"]

    make_directories([dataset_dir])
    seed_everything(seed=42)

    # logging
    current_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    log_dir_base = "logger"
    log_dir = f"{log_dir_base}/{current_time}"
    init_logging_handler(log_dir_base, current_time)

    # dump config in logger
    with open(f"{log_dir}/config.json", "w") as fp:
        json.dump(train_config, fp)

    # saving model path location
    model_path_pretrained = os.path.join(
        f"{log_dir}/checkpoint",
        f"{network_type}_pretrained_{dataset_name}_{current_time}.pt",
    )

    model_path_finetune = os.path.join(
        f"{log_dir}/checkpoint",
        f"{network_type}_finetune_{dataset_name}_{current_time}.pt",
    )

    ########################## dataloading ##########################

    # setup default dataset
    default_dataset_setup = DefaultDatasetSetupSSL()
    default_dataset_setup.setup(dataset_name=dataset_name)
    default_dataset_setup.setup_transforms(image_size=image_size)

    # trainset
    train_dataset = default_dataset_setup.get_dataset(mode="train")
    default_dataset_setup.visualize_dataset(train_dataset)

    # split in train and valid set
    split_ratio = 0.25  # 0.25
    valid_len = int(split_ratio * len(train_dataset))
    train_len = len(train_dataset) - valid_len

    train_dataset, val_set = random_split(train_dataset, [train_len, valid_len])

    train_loader = DataLoader(
        dataset=train_dataset, batch_size=batch_size, shuffle=True, num_workers=8
    )

    val_loader = DataLoader(
        dataset=val_set,
        batch_size=batch_size,
        shuffle=True,
        num_workers=8
    )

    # Load test dataset
    # testset = default_dataset_setup.get_dataset(mode="val")
    # test_loader = DataLoader(dataset=testset, batch_size=batch_size, shuffle=True)

    # size check
    sample = next(iter(train_loader))
    print("num of classes: ", num_classes)
    print(sample[0].shape)

    ########################## pretrain ##########################

    # Create pretrain model
    resnet = torchvision.models.resnet18()
    backbone = nn.Sequential(*list(resnet.children())[:-1])
    model = BYOL(backbone)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)

    # model = CustomResNet(num_channels, device="cuda")
    # in_features = model.get_fc_in_features()
    # head = nn.Identity()
    # model.add_head(head=head, freeze_backbone=False)
    # model.summarize()

    # optimizer and loss function for pretrain
    # optimizer_pretrain = optim.Adam(model.parameters(), lr=learning_rate)
    optimizer_pretrain = torch.optim.SGD(model.parameters(), lr=0.06)
    
    # criterion
    # criterion_pretrain = ContrastiveLossEmbedding(margin, device=device)
    criterion_pretrain = NegativeCosineSimilarity()
    # criterion_pretrain = SimCLR_Loss(batch_size=batch_size, temperature=temperature)

    # pretraining
    train_byol(
        epochs=epochs_pretrained,
        model=model,
        device=device,
        train_loader=train_loader,
        criterion=criterion_pretrain,
        optimizer=optimizer_pretrain,
    )
    # train_contrastive_pair(
    #     epochs=epochs_pretrained,
    #     model=model,
    #     device=device,
    #     train_loader=train_loader,
    #     criterion=criterion_pretrain,
    #     optimizer=optimizer_pretrain,
    #     saved_model_path=model_path_pretrained,
    #     batch_size=batch_size,
    # )

    ########################## finetune ##########################

    # Create finetune model
    # in_features = model.get_last_layer_features()
    in_features = model.get_second_last_in_features()

    finetune_head = nn.Sequential(
        nn.Linear(512, 256), nn.ReLU(), nn.Linear(256, num_classes)
    )
    model = model.add_head(finetune_head, freeze_backbone=True)
    summary(model, input_size=(1, 1, 224, 224), device="cuda")
    model.to(device="cuda")

    # optimizer and loss function for finetuning
    optimizer_finetune = optim.Adam(model.parameters(), lr=learning_rate)
    criterion_finetune = nn.CrossEntropyLoss()

    # finetuning loop
    train_simplistic(
        epochs=epochs_finetuned,
        model=model,
        device="cuda",
        train_loader=train_loader,
        criterion=criterion_finetune,
        optimizer=optimizer_finetune,
        saved_model_path=model_path_finetune,
        valid_loader=val_loader,
    )

    ########################## inference ##########################

    # perform inference on finetuned model
    infer_obj = InferenceSSL(
        model,
        val_loader,
        device,
        num_classes,
        val_set,
        dataset_name,
        labels_map=classes,
        image_size=image_size,
        channels=num_channels,
        destination_dir="data",
        log_dir=log_dir,  # log_dir
    )

    infer_obj.infer_plot_roc()
    infer_obj.generate_plot_confusion_matrix()

    ########################## EOF ##########################

    # seed_everything(seed=42)

    # train_loader = DataLoader(
    #     dataset=trainset,
    #     batch_size=batch_size,
    #     shuffle=True,
    #     num_workers=num_workers,
    # )

    # num_classes = len(classes)
    # print(num_classes)
    # print(f"Train Data: {len(trainset)}")
    # print(f"Val Data: {len(testset)}")

    # # Transformer model
    # model = TransformerModels(
    #     transformer_type=train_config["network_type"],
    #     num_channels=train_config["channels"],
    #     num_classes=num_classes,
    #     img_size=image_size,
    #     **train_config["network_config"],
    # )

    # summary(model, input_size=(train_config["batch_size"], 1, image_size, image_size))

    # # loss function
    # criterion = nn.CrossEntropyLoss()

    # # optimizer
    # optimizer = optim.AdamW(
    #     model.parameters(),
    #     lr=optimizer_config["lr"],
    #     weight_decay=optimizer_config["weight_decay"],
    # )


if __name__ == "__main__":
    main()
