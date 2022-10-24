# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/models.mlp.ipynb.

# %% auto 0
__all__ = ['MLP']

# %% ../../nbs/models.mlp.ipynb 5
import torch
import torch.nn as nn

from ..losses.pytorch import MAE
from ..common._base_windows import BaseWindows

# %% ../../nbs/models.mlp.ipynb 6
class MLP(BaseWindows):
    """ MLP

    Simple Multi Layer Perceptron architecture (MLP). 
    This deep neural network has constant units through its layers, each with
    ReLU non-linearities, it is trained using ADAM stochastic gradient descent.
    The network accepts static, historic and future exogenous data, flattens 
    the inputs and learns fully connected relationships against the target variable.

    **Parameters:**<br>
    `h`: int, forecast horizon.<br>
    `input_size`: int, considered autorregresive inputs (lags), y=[1,2,3,4] input_size=2 -> lags=[1,2].<br>
    `n_layers`: int, number of layers for the MLP.<br>
    `hidden_size`: int, number of units for each layer of the MLP.<br>
    `stat_exog_list`: str list, static exogenous columns.<br>
    `hist_exog_list`: str list, historic exogenous columns.<br>
    `futr_exog_list`: str list, future exogenous columns.<br>
    `loss`: PyTorch module, instantiated train loss class from [losses collection](https://nixtla.github.io/neuralforecast/losses.pytorch.html).<br>
    `learning_rate`: float, initial optimization learning rate (0,1).<br>
    `batch_size`: int=32, number of differentseries in each batch.<br>
    `windows_batch_size`: int=None, windows sampled from rolled data, if None uses all.<br>
    `step_size`: int=1, step size between each window of temporal data.<br>
    `scaler_type`: str=None, type of scaler for temporal inputs normalization see [temporal scalers](https://nixtla.github.io/neuralforecast/common.scalers.html).<br>
    `random_seed`: int=1, random_seed for pytorch initializer and numpy generators.<br>
    `num_workers_loader`: int=os.cpu_count(), workers to be used by `TimeSeriesDataLoader`.<br>
    `drop_last_loader`: bool=False, if True `TimeSeriesDataLoader` drops last non-full batch.<br>
    `**trainer_kwargs`: int,  keyword trainer arguments inherited from [PyTorch Lighning's trainer](https://pytorch-lightning.readthedocs.io/en/stable/api/pytorch_lightning.trainer.trainer.Trainer.html?highlight=trainer).<br>    
    """
    def __init__(self,
                 h,
                 input_size,
                 num_layers=2,
                 hidden_size=1024,
                 futr_exog_list = None,
                 hist_exog_list = None,
                 stat_exog_list = None,
                 loss=MAE(),
                 learning_rate=1e-3,
                 batch_size=32,
                 windows_batch_size=1024,
                 step_size=1,
                 scaler_type=None,
                 random_seed=1,
                 num_workers_loader=0,
                 drop_last_loader=False,
                 **trainer_kwargs):

        # Inherit BaseWindows class
        super(MLP, self).__init__(h=h,
                                  input_size=input_size,
                                  loss=loss,
                                  learning_rate=learning_rate,
                                  batch_size=batch_size,
                                  windows_batch_size=windows_batch_size,
                                  step_size=step_size,
                                  scaler_type=scaler_type,
                                  futr_exog_list=futr_exog_list,
                                  hist_exog_list=hist_exog_list,
                                  stat_exog_list=stat_exog_list,
                                  num_workers_loader=num_workers_loader,
                                  drop_last_loader=drop_last_loader,
                                  random_seed=random_seed,
                                  **trainer_kwargs)

        # Architecture
        self.num_layers = num_layers
        self.hidden_size = hidden_size

        self.futr_input_size = len(self.futr_exog_list)
        self.hist_input_size = len(self.hist_exog_list)
        self.stat_input_size = len(self.stat_exog_list)

        input_size_first_layer = input_size + self.hist_input_size*input_size + \
                                 self.futr_input_size*(input_size + h) + self.stat_input_size

        # MultiLayer Perceptron
        layers = [nn.Linear(in_features=input_size_first_layer, out_features=hidden_size)]
        for i in range(num_layers - 1):
            layers += [nn.Linear(in_features=hidden_size, out_features=hidden_size)]
        self.mlp = nn.ModuleList(layers)

        # Adapter with Loss dependent dimensions
        self.out = nn.Linear(in_features=hidden_size, 
                             out_features=h * self.loss.outputsize_multiplier)

    def forward(self, windows_batch):

        # Parse windows_batch
        insample_y    = windows_batch['insample_y']
        futr_exog     = windows_batch['futr_exog']
        hist_exog     = windows_batch['hist_exog']
        stat_exog     = windows_batch['stat_exog']

        # Flatten MLP inputs [B, L+H, C] -> [B, (L+H)*C]
        # Contatenate [ Y_t, | X_{t-L},..., X_{t} | F_{t-L},..., F_{t+H} | S ]
        batch_size = len(insample_y)
        if self.hist_input_size > 0:
            insample_y = torch.cat(( insample_y, hist_exog.reshape(batch_size,-1) ), dim=1)

        if self.futr_input_size > 0:
            insample_y = torch.cat(( insample_y, futr_exog.reshape(batch_size,-1) ), dim=1)

        if self.stat_input_size > 0:
            insample_y = torch.cat(( insample_y, stat_exog.reshape(batch_size,-1) ), dim=1)

        y_pred = insample_y.clone()
        for layer in self.mlp:
             y_pred = torch.relu(layer(y_pred))
        y_pred = self.out(y_pred)
        y_pred = self.loss.adapt_output(y_pred)
        return y_pred
