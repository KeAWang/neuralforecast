# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/common.base_windows.ipynb.

# %% auto 0
__all__ = ['BaseWindows']

# %% ../../nbs/common.base_windows.ipynb 4
import random

import numpy as np
import torch
import torch.nn as nn
import pytorch_lightning as pl
from pytorch_lightning.callbacks import TQDMProgressBar

from ..tsdataset import TimeSeriesDataModule

# %% ../../nbs/common.base_windows.ipynb 5
class BaseWindows(pl.LightningModule):

    def __init__(self, h, 
                 loss,
                 batch_size=32,
                 normalize=False,
                 futr_exog_list=None,
                 hist_exog_list=None,
                 stat_exog_list=None,
                 num_workers_loader=0,
                 drop_last_loader=False,
                 random_seed=1, 
                 **trainer_kwargs):
        super(BaseWindows, self).__init__()
        self.random_seed = random_seed
        pl.seed_everything(self.random_seed, workers=True)
        
        # Padder to complete train windows
        self.h = h
        self.padder = nn.ConstantPad1d(padding=(0, self.h), value=0)
        
        # Loss
        self.loss = loss
        self.normalize = normalize
        
        # Variables
        self.futr_exog_list = futr_exog_list if futr_exog_list is not None else []
        self.hist_exog_list = hist_exog_list if hist_exog_list is not None else []
        self.stat_exog_list = stat_exog_list if stat_exog_list is not None else []

        # Base arguments
        self.windows_batch_size: int = None
        
        # Fit arguments
        self.val_size: int = 0
        self.test_size: int = 0

        # Predict arguments
        self.step_size: int = 1

        # Model state
        self.decompose_forecast = False
        
        # Trainer
        # we need to instantiate the trainer each time we want to use it
        self.trainer_kwargs = {**trainer_kwargs}
        if self.trainer_kwargs.get('callbacks', None) is None:
            self.trainer_kwargs = {**{'callbacks': [TQDMProgressBar()], **trainer_kwargs}}
        else:
            self.trainer_kwargs = trainer_kwargs

        # Add GPU accelerator if available
        if self.trainer_kwargs.get('accelerator', None) is None:
            if torch.cuda.is_available():
                self.trainer_kwargs['accelerator'] = "gpu"
        if self.trainer_kwargs.get('devices', None) is None:
            if torch.cuda.is_available():
                self.trainer_kwargs['devices'] = -1
        
        # DataModule arguments
        self.batch_size = batch_size
        self.num_workers_loader = num_workers_loader
        self.drop_last_loader = drop_last_loader
        
    
    def on_fit_start(self):
        torch.manual_seed(self.random_seed)
        np.random.seed(self.random_seed)
        random.seed(self.random_seed)
        
    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=self.learning_rate)

    def _create_windows(self, batch, step):
        # Parse common data
        window_size = self.input_size + self.h
        temporal_cols = batch['temporal_cols']
        temporal = batch['temporal']

        if step == 'train':
            if self.val_size + self.test_size > 0:
                cutoff = -self.val_size - self.test_size
                temporal = temporal[:, :, :cutoff]

            temporal = self.padder(temporal)
            windows = temporal.unfold(dimension=-1, 
                                      size=window_size, 
                                      step=self.step_size)

            # [batch, channels, windows, window_size] 0, 1, 2, 3
            # -> [batch * windows, window_size, channels] 0, 2, 3, 1
            windows = windows.permute(0, 2, 3, 1).contiguous()
            windows = windows.reshape(-1, window_size, len(temporal_cols))

            available_idx = temporal_cols.get_loc('available_mask')
            sample_condition = windows[:, -self.h:, available_idx]
            sample_condition = torch.sum(sample_condition, axis=1)
            sample_condition = (sample_condition > 0)
            windows = windows[sample_condition]

            # Sample windows
            n_windows = len(windows)
            if self.windows_batch_size is not None:
                w_idxs = np.random.choice(n_windows, 
                                          size=self.windows_batch_size,
                                          replace=(n_windows < self.windows_batch_size))
                windows = windows[w_idxs]

            # think about interaction available * sample mask
            windows_batch = dict(temporal=windows,
                                 static=None,
                                 temporal_cols=temporal_cols)
            return windows_batch

        elif step in ['predict', 'val']:

            if step == 'predict':
                predict_step_size = self.predict_step_size
                cutoff = - self.input_size - self.test_size
                temporal = batch['temporal'][:, :, cutoff:]

            elif step == 'val':
                predict_step_size = self.step_size
                cutoff = -self.input_size - self.val_size - self.test_size
                if self.test_size > 0:
                    temporal = batch['temporal'][:, :, cutoff:-self.test_size]
                else:
                    temporal = batch['temporal'][:, :, cutoff:]

            if (step=='predict') and (self.test_size==0) and (len(self.futr_exog_list)==0):
               temporal = self.padder(temporal)

            windows = temporal.unfold(dimension=-1,
                                      size=window_size,
                                      step=predict_step_size)

            # [batch, channels, windows, window_size] 0, 1, 2, 3
            # -> [batch * windows, window_size, channels] 0, 2, 3, 1
            windows = windows.permute(0, 2, 3, 1).contiguous()
            windows = windows.reshape(-1, window_size, len(temporal_cols))
            windows_batch = dict(temporal=windows,
                                 static=None,
                                 temporal_cols=temporal_cols)
            return windows_batch
        else:
            raise ValueError(f'Unknown step {step}')
            
    def _normalization(self, windows):
        # windows are already filtered by train/validation/test
        # from the `create_windows_method` nor leakage risk
        temporal = windows['temporal']           # B, L+H, C
        temporal_cols = windows['temporal_cols'] # B, L+H, C
        
        # to avoid leakage compute means on the lags only
        temporal_y = temporal[:, :-self.h, temporal_cols.get_loc('y')]
        temporal_mask = temporal[:, :-self.h, temporal_cols.get_loc('available_mask')]
        
        # Take means in the window L+H
        available_sum = torch.sum(temporal_mask, dim=1, keepdim=True)
                
        # Protection: when no observations are available denom = 1
        mask_safe = available_sum.clone()
        mask_safe[available_sum==0] = 1
        
        y_means = torch.sum(temporal_y * temporal_mask,
                            dim=1, keepdim=True) / mask_safe
        y_stds = torch.sqrt(torch.sum(temporal_mask*(temporal_y-y_means)**2,
                                      dim=1, keepdim=True)/ mask_safe )

        # Protection: when no variance or unavailable data change stds=1
        y_stds[available_sum==0] = 1.0
        y_stds[y_stds==0] = 1.0

        # Normalize all target variable and replace in windows dict
        all_y = temporal[:, :, temporal_cols.get_loc('y')]
        all_y = (all_y - y_means) / y_stds
        temporal[:, :, temporal_cols.get_loc('y')] = all_y
        
        windows['temporal'] = temporal
        
        return windows, y_means, y_stds

    def _inv_normalization(self, y_hat, y_means, y_stds):
        # Receives window predictions [B, H, output]
        # Broadcasts outputs and inverts normalization
        if self.loss.outputsize_multiplier>1:
            y_stds = y_stds.unsqueeze(-1)
            y_means = y_means.unsqueeze(-1)
        return y_stds * y_hat + y_means

    def _parse_windows(self, batch, windows):
        # Filter insample lags from outsample horizon
        y_idx = batch['temporal_cols'].get_loc('y')
        mask_idx = batch['temporal_cols'].get_loc('available_mask')
        insample_y = windows['temporal'][:, :-self.h, y_idx]
        insample_mask = windows['temporal'][:, :-self.h, mask_idx]
        outsample_y = windows['temporal'][:, -self.h:, y_idx]
        outsample_mask = windows['temporal'][:, -self.h:, mask_idx]

        # Filter historic exogenous variables
        if len(self.hist_exog_list):
            hist_exog_idx = windows['temporal_cols'].get_indexer(self.hist_exog_list)
            hist_exog = windows['temporal'][:, :-self.h, hist_exog_idx]
        else:
            hist_exog = None
        
        # Filter future exogenous variables
        if len(self.futr_exog_list):
            futr_exog_idx = windows['temporal_cols'].get_indexer(self.futr_exog_list)
            futr_exog = windows['temporal'][:, :, futr_exog_idx]
        else:
            futr_exog = None
        # Filter static variables
        if len(self.stat_exog_list):
            static_idx = windows['static_cols'].get_indexer(self.stat_exog_list)
            stat_exog = windows['static'][:, 0, static_idx]
        else:
            stat_exog = None

        return insample_y, insample_mask, outsample_y, outsample_mask, \
               hist_exog, futr_exog, stat_exog

    def training_step(self, batch, batch_idx):        
        # Create windows [Ws, L+H, C]
        windows = self._create_windows(batch, step='train')
        
        # Normalize windows
        if self.normalize:
            windows, *_ = self._normalization(windows)

        # Parse windows
        insample_y, insample_mask, outsample_y, outsample_mask, \
               hist_exog, futr_exog, stat_exog = self._parse_windows(batch, windows)

        windows_batch = dict(insample_y=insample_y, # [Ws, L]
                             insample_mask=insample_mask, # [Ws, L]
                             futr_exog=futr_exog, # [Ws, L+H]
                             hist_exog=hist_exog, # [Ws, L]
                             stat_exog=stat_exog) # [Ws, 1]

        y_hat = self(windows_batch)
        loss = self.loss(y=outsample_y, y_hat=y_hat, mask=outsample_mask)
        self.log('train_loss', loss, prog_bar=True, on_epoch=True)
        return loss

    def validation_step(self, batch, batch_idx):
        if self.val_size == 0:
            return np.nan
        
        # Create windows [Ws, L+H, C]
        windows = self._create_windows(batch, step='val')
        
        # Normalize windows
        if self.normalize:
            windows, *_ = self._normalization(windows)

        # Parse windows
        insample_y, insample_mask, outsample_y, outsample_mask, \
               hist_exog, futr_exog, stat_exog = self._parse_windows(batch, windows)

        windows_batch = dict(insample_y=insample_y, # [Ws, L]
                             insample_mask=insample_mask, # [Ws, L]
                             futr_exog=futr_exog, # [Ws, L+H]
                             hist_exog=hist_exog, # [Ws, L]
                             stat_exog=stat_exog) # [Ws, 1]

        y_hat = self(windows_batch)
        loss = self.loss(y=outsample_y, y_hat=y_hat, mask=outsample_mask)
        self.log('val_loss', loss, prog_bar=True, on_epoch=True)
        return loss
    
    def validation_epoch_end(self, outputs):
        if self.val_size == 0:
            return
        avg_loss = torch.stack(outputs).mean()
        self.log("ptl/val_loss", avg_loss)
    
    def predict_step(self, batch, batch_idx):        
        # Create windows [Ws, L+H, C]
        windows = self._create_windows(batch, step='predict')

        # Normalize windows
        if self.normalize:
            windows, y_means, y_stds = self._normalization(windows)

        # Parse windows
        insample_y, insample_mask, _, _, \
               hist_exog, futr_exog, stat_exog = self._parse_windows(batch, windows)

        windows_batch = dict(insample_y=insample_y, # [Ws, L]
                             insample_mask=insample_mask, # [Ws, L]
                             futr_exog=futr_exog, # [Ws, L+H]
                             hist_exog=hist_exog, # [Ws, L]
                             stat_exog=stat_exog) # [Ws, 1]

        y_hat = self(windows_batch)

        # Inv Normalize
        if self.normalize:
            y_hat = self._inv_normalization(y_hat, y_means, y_stds)
        return y_hat
    
    def fit(self, dataset, val_size=0, test_size=0):
        """
        Fits Model.
        
        **Parameters:**<br>
        `dataset`: TimeSeriesDataset.<br>
        `trainer`: pl.Trainer.<br>
        `val_size`: int, validation size.<br>
        `test_size`: int, test size.<br>
        `data_kwargs`: extra arguments to be passed to TimeSeriesDataModule.
        """
        self.val_size = val_size
        self.test_size = test_size
        datamodule = TimeSeriesDataModule(
            dataset, 
            batch_size=self.batch_size,
            num_workers=self.num_workers_loader,
            drop_last=self.drop_last_loader
        )
        
        trainer = pl.Trainer(**self.trainer_kwargs)
        trainer.fit(self, datamodule=datamodule)
        
    def predict(self, dataset, test_size=None, step_size=1, **data_kwargs):
        """
        Predicts Model.

        **Parameters:**<br>
        `dataset`: TimeSeriesDataset.<br>
        `trainer`: pl.Trainer.<br>
        `step_size`: int, Step size between each window.<br>
        `data_kwargs`: extra arguments to be passed to TimeSeriesDataModule.
        """
        self.predict_step_size = step_size
        self.decompose_forecast = False
        datamodule = TimeSeriesDataModule(dataset, **data_kwargs)
        trainer = pl.Trainer(**self.trainer_kwargs)
        fcsts = trainer.predict(self, datamodule=datamodule)        
        fcsts = torch.vstack(fcsts).numpy().flatten()    
        fcsts = fcsts.reshape(-1, self.loss.outputsize_multiplier)
        return fcsts

    def decompose(self, dataset, step_size=1, **data_kwargs):
        """
        Predicts with decomposition.
        
        **Parameters:**<br>
        `dataset`: TimeSeriesDataset.<br>
        `trainer`: pl.Trainer.<br>
        `step_size`: int, Step size between each window.<br>
        `data_kwargs`: extra arguments to be passed to TimeSeriesDataModule.
        """
        self.predict_step_size = step_size
        self.decompose_forecast = True
        datamodule = TimeSeriesDataModule(dataset, **data_kwargs)
        trainer = pl.Trainer(**self.trainer_kwargs)
        fcsts = trainer.predict(self, datamodule=datamodule)
        self.decompose_forecast = False # Default decomposition back to false
        return torch.vstack(fcsts).numpy()

    def forward(self, insample_y, insample_mask):
        raise NotImplementedError('forward')

    def set_test_size(self, test_size):
        self.test_size = test_size
