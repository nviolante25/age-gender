import hydra
from hydra.utils import instantiate

import torch
import torch.nn as nn
from torch.optim import Adam
import numpy as np
import pytorch_lightning as pl
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.metrics.functional.classification import accuracy
from dataloader import DataModule


from utils.confusion_matrix import confusion_matrix_plot_as_array
from models.age_gender_net import AgeGenderNet
from models.nddr_net import NDDRNet
from models.age_net import AgeNet
from models.gender_net import GenderNet

import warnings
warnings.simplefilter("ignore")

class MutiTaskNet(pl.LightningModule):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.loss_age = nn.NLLLoss()
        self.loss_gender = nn.NLLLoss()

    def configure_optimizers(self):
        return Adam(self.parameters(), lr=0.005)

    def forward(self, x):
        return self.net(x)
    
    def training_step(self, batch, batch_idx):
        output = self.compute_output(batch, stage="train")
        return output

    def validation_step(self, batch, batch_idx):
        output = self.compute_output(batch, stage="val")
        return output

    def compute_output(self, batch, stage):
        image, age_group, gender = batch
        age_group_probs, gender_probs = self.net(image)
        age_loss = self.loss_age(torch.log(age_group_probs), age_group)
        gender_loss = self.loss_gender(torch.log(gender_probs), gender)
        loss = age_loss + gender_loss
        self.log_dict({f"{stage}/loss/age": age_loss, f"{stage}/loss/gender": gender_loss, f"{stage}/loss/total": loss})
        output = {"loss": loss,
                  "age": (age_group, age_group_probs.argmax(dim=1)),
                  "gender": (gender, gender_probs.argmax(dim=1))}
        return output

    def training_epoch_end(self, outputs):
        if self.is_time_to_log():
            self.log_epoch_results(outputs, stage="train")

    def validation_epoch_end(self, outputs):
        if self.is_time_to_log():
            self.log_epoch_results(outputs, stage="val")

    def is_time_to_log(self, log_freq=10):
        return self.current_epoch % log_freq == 0

    def log_epoch_results(self, outputs, stage):
        age_pred, age_true, gender_pred, gender_true = self.get_epoch_results(outputs)

        age_labels = ["0-10", "11-20", "21-30", "31-40", "41-50", "51-60", "60-"]
        cm_age = confusion_matrix_plot_as_array(age_true, age_pred, age_labels)
        cm_gender = confusion_matrix_plot_as_array(gender_true, gender_pred, ["Male", "Female"])

        # Tensorboard logs
        age_accuracy = accuracy(torch.tensor(age_pred).to(self.device), (torch.tensor(age_true).to(self.device))).item()
        gender_accuracy = accuracy(torch.tensor(gender_pred).to(self.device), (torch.tensor(gender_true).to(self.device))).item()

        tb = self.logger.experiment
        tb.add_scalar(f"{stage}/Age accuracy", age_accuracy, self.current_epoch)
        tb.add_scalar(f"{stage}/Gender accuracy", gender_accuracy, self.current_epoch)
        tb.add_image(f"{stage}/Age", cm_age, self.current_epoch, dataformats="HWC")
        tb.add_image(f"{stage}/Gender", cm_gender, self.current_epoch, dataformats="HWC")

    def get_epoch_results(self, outputs):
        age_pred = []
        age_true = []
        gender_pred = []
        gender_true = []
        for i, output in enumerate(outputs):
            age_true.extend(self.tensor_to_array(output["age"][0]))
            age_pred.extend(self.tensor_to_array(output["age"][1]))
            gender_true.extend(self.tensor_to_array(output["gender"][0]))
            gender_pred.extend(self.tensor_to_array(output["gender"][1]))
        return age_pred,age_true,gender_pred,gender_true

    @staticmethod
    def tensor_to_array(tensor):
        return np.array(tensor.cpu())


@hydra.main(config_path=r".\configs\train.yaml")
def main(cfg):
    models = {"AgeGenderNet": AgeGenderNet,
              "NDDRNet": NDDRNet,
              "AgeNet": AgeNet,
              "GenderNet": GenderNet}
              
    if cfg.model.name not in models:
        raise ValueError(f"{cfg.model.name} is not a valid model")

    datamodule = DataModule.create(cfg.dataset)

    checkpoint = ModelCheckpoint(**cfg.checkpoint)
    logger = TensorBoardLogger(save_dir=cfg.logger.save_dir, 
                               name=f"{cfg.model.name}_{cfg.model.backbone}")

    trainer = pl.Trainer(**cfg.trainer,
                         callbacks=[checkpoint],
                         logger=logger)

    net = models[cfg.model.name].create(cfg.model)
    if cfg.model.multi:
        model = MutiTaskNet(net)
    else:
        model = net
    trainer.fit(model, datamodule)

if __name__ == "__main__":
    main()
