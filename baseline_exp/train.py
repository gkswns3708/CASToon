from facenet_pytorch import (
    MTCNN,
    InceptionResnetV1,
    fixed_image_standardization,
    training,
)
import torch
from torch.utils.data import DataLoader, SubsetRandomSampler
from torch import optim
from torch.optim.lr_scheduler import MultiStepLR
from torch.utils.tensorboard import SummaryWriter
from torchvision import datasets, transforms

import numpy as np
import os

from datetime import datetime
from pytz import timezone

import argparse
import json


device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
print("Running on device: {}".format(device))


def main(cfgs):
    mtcnn = MTCNN(
        image_size=cfgs["resize"],
        margin=80,
        min_face_size=20,
        thresholds=[0.6, 0.7, 0.7],
        factor=0.709,
        post_process=True,
        device=device,
    )

    dataset = datasets.ImageFolder(cfgs["data_dir"])
    dataset.samples = [
        (p, p.replace(cfgs["data_dir"], cfgs["data_dir"] + "_cropped"))
        for p, _ in dataset.samples
    ]

    loader = DataLoader(
        dataset,
        num_workers=cfgs["num_workers"],
        batch_size=cfgs["batch_size"],
        collate_fn=training.collate_pil,
    )

    for i, (x, y) in enumerate(loader):
        mtcnn(x, save_path=y)
        print("\rBatch {} of {}".format(i + 1, len(loader)), end="")

    # Remove mtcnn to reduce GPU memory usage
    del mtcnn

    resnet = InceptionResnetV1(
        classify=True, pretrained="vggface2", num_classes=len(dataset.class_to_idx)
    ).to(device)

    optimizer = optim.Adam(resnet.parameters(), lr=0.001)
    scheduler = MultiStepLR(optimizer, [5, 10])

    trans = transforms.Compose(
        [
            transforms.RandomHorizontalFlip(),
            np.float32,
            transforms.ToTensor(),
            fixed_image_standardization,
        ]
    )
    dataset = datasets.ImageFolder(cfgs["data_dir"], transform=trans)
    img_inds = np.arange(len(dataset))
    np.random.shuffle(img_inds)
    train_inds = img_inds[: int(cfgs["train_ratio"] * len(img_inds))]
    val_inds = img_inds[int(cfgs["train_ratio"] * len(img_inds)) :]

    train_loader = DataLoader(
        dataset,
        num_workers=cfgs["num_workers"],
        batch_size=cfgs["batch_size"],
        sampler=SubsetRandomSampler(train_inds),
    )
    val_loader = DataLoader(
        dataset,
        num_workers=cfgs["num_workers"],
        batch_size=cfgs["batch_size"],
        sampler=SubsetRandomSampler(val_inds),
    )

    loss_fn = torch.nn.CrossEntropyLoss()
    metrics = {"fps": training.BatchTimer(), "acc": training.accuracy}

    writer = SummaryWriter()
    writer.iteration, writer.interval = 0, 10

    print("\n\nInitial")
    print("-" * 10)
    resnet.eval()
    training.pass_epoch(
        resnet,
        loss_fn,
        val_loader,
        batch_metrics=metrics,
        show_running=True,
        device=device,
        writer=writer,
    )

    for epoch in range(cfgs["epochs"]):
        print(
            f"\nEpoch: {epoch}/{cfgs['epochs']}, Time: {datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print("-" * 10)

        resnet.train()
        training.pass_epoch(
            resnet,
            loss_fn,
            train_loader,
            optimizer,
            scheduler,
            batch_metrics=metrics,
            show_running=True,
            device=device,
            writer=writer,
        )

        resnet.eval()
        training.pass_epoch(
            resnet,
            loss_fn,
            val_loader,
            batch_metrics=metrics,
            show_running=True,
            device=device,
            writer=writer,
        )
        if epoch % 2 == 0:
            torch.save(
                resnet.state_dict(),
                os.path.join(
                    cfgs["model_path"],
                    f"{datetime.now(timezone('Asia/Seoul')).strftime('%Y-%m-%d_%H_%M_%S')}_{epoch}.pth",
                ),
            )

    writer.close()


if __name__ == "__main__":
    args = argparse.ArgumentParser(description="Facenet Train")
    args.add_argument(
        "-c",
        "--config",
        default="./train_config.json",
        type=str,
        help="config file path (default: None)",
    )
    args = args.parse_args()
    config_path = args.config
    with open(config_path, "r") as f:
        cfgs = json.load(f)

    main(cfgs)
