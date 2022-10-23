# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/common.encoders.ipynb.

# %% auto 0
__all__ = ['ACTIVATIONS', 'Chomp1d', 'CausalConv1d', 'TemporalConvolutionEncoder']

# %% ../../nbs/common.encoders.ipynb 3
import torch.nn as nn

# %% ../../nbs/common.encoders.ipynb 6
ACTIVATIONS = ['ReLU','Softplus','Tanh','SELU',
               'LeakyReLU','PReLU','Sigmoid']

class Chomp1d(nn.Module):
    """ Chomp1d

    Receives `x` input of dim [N,C,T], and trims it so that only
    'time available' information is used. 
    Used by one dimensional causal convolutions `CausalConv1d`.

    **Parameters:**<br>
    `horizon`: int, length of outsample values to skip.
    """
    def __init__(self, horizon):
        super(Chomp1d, self).__init__()
        self.horizon = horizon

    def forward(self, x):
        return x[:, :, :-self.horizon].contiguous()


class CausalConv1d(nn.Module):
    """ Causal Convolution 1d

    Receives `x` input of dim [N,C_in,T], and computes a causal convolution
    in the time dimension. Skipping the H steps of the forecast horizon, through
    its dilation.
    Consider a batch of one element, the dilated convolution operation on the
    $t$ time step is defined:

    $\mathrm{Conv1D}(\mathbf{x},\mathbf{w})(t) = (\mathbf{x}_{[*d]} \mathbf{w})(t) = \sum^{K}_{k=1} w_{k} \mathbf{x}_{t-dk}$

    where $d$ is the dilation factor, $K$ is the kernel size, $t-dk$ is the index of
    the considered past observation. The dilation effectively applies a filter with skip
    connections. If $d=1$ one recovers a normal convolution.

    **Parameters:**<br>
    `in_channels`: int, dimension of `x` input's initial channels.<br> 
    `out_channels`: int, dimension of `x` outputs's channels.<br> 
    `activation`: str, identifying activations from PyTorch activations.
        select from 'ReLU','Softplus','Tanh','SELU', 'LeakyReLU','PReLU','Sigmoid'.<br>
    `padding`: int, number of zero padding used to the left.<br>
    `kernel_size`: int, convolution's kernel size.<br>
    `dilation`: int, dilation skip connections.<br>
    
    **Returns:**<br>
    `x`: tensor, torch tensor of dim [N,C_out,T] activation(conv1d(inputs, kernel) + bias). <br>
    """
    def __init__(self, in_channels, out_channels, kernel_size,
                 padding, dilation, activation, stride:int=1):
        super(CausalConv1d, self).__init__()
        assert activation in ACTIVATIONS, f'{activation} is not in {ACTIVATIONS}'
        
        self.conv       = nn.Conv1d(in_channels=in_channels, out_channels=out_channels, 
                                    kernel_size=kernel_size, stride=stride, padding=padding,
                                    dilation=dilation)
        
        self.chomp      = Chomp1d(padding)
        self.activation = getattr(nn, activation)()
        self.causalconv = nn.Sequential(self.conv, self.chomp, self.activation)
    
    def forward(self, x):
        return self.causalconv(x)

# %% ../../nbs/common.encoders.ipynb 8
class TemporalConvolutionEncoder(nn.Module):
    """ Temporal Convolution Encoder

    Receives `x` input of dim [N,T,C_in], permutes it to  [N,C_in,T]
    applies a deep stack of exponentially dilated causal convolutions.
    The exponentially increasing dilations of the convolutions allow for 
    the creation of weighted averages of exponentially large long-term memory.

    **Parameters:**<br>
    `in_channels`: int, dimension of `x` input's initial channels.<br> 
    `out_channels`: int, dimension of `x` outputs's channels.<br> 
    `activation`: str, identifying activations from PyTorch activations.
        select from 'ReLU','Softplus','Tanh','SELU', 'LeakyReLU','PReLU','Sigmoid'.<br>

    **Returns:**<br>
    `x`: tensor, torch tensor of dim [N,T,C_out].<br>
    """
    # TODO: Add dilations parameter and change layers declaration to for loop
    def __init__(self, in_channels, out_channels, kernel_size, 
                 activation:str='ReLU', stride:int=1):
        super(TemporalConvolutionEncoder, self).__init__()
        layers = [CausalConv1d(in_channels=in_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*1, 
                               activation=activation, dilation=1),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*2, 
                               activation=activation, dilation=2),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*4, 
                               activation=activation, dilation=4),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*8, 
                               activation=activation, dilation=8),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*16, 
                               activation=activation, dilation=16),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels, 
                               kernel_size=kernel_size, padding=(kernel_size-1)*32, 
                               activation=activation, dilation=32)]
        self.tcn = nn.Sequential(*layers)

    def forward(self, x):
        # [N,T,C_in] -> [N,C_in,T] -> [N,T,C_out]
        x = x.permute(0, 2, 1).contiguous()
        x = self.tcn(x)
        x = x.permute(0, 2, 1).contiguous()
        return x