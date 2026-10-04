"""Light-ASD, the active speaker detector of people.py: which face's mouth moves with the speech.

The network of Liao, Duan, Zhang, Li and Zhang, "A Light Weight Model for Active Speaker Detection" (CVPR 2023,
https://github.com/Junhua-Liao/Light-ASD, MIT licence -- copyright (c) 2023 Liao Junhua; the licence text is in
face_models/LIGHT_ASD_LICENSE), the original code joined into one file and trimmed to inference: a visual encoder
over 112x112 grey face crops at 25 fps, an audio encoder over 13 MFCCs at 100 per second, a forward / backward GRU
over their sum and the classifier's speaking logit per video frame. The weights (face_models/light_asd_talkset.model,
1M parameters) are the authors' model fine-tuned on TalkSet for videos in the wild. Needs PyTorch.
"""
from __future__ import annotations

import torch
from torch import nn


class _AudioBlock(nn.Module):
    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.relu = nn.ReLU()
        self.m_3 = nn.Conv2d(cin, cout, kernel_size=(3, 1), padding=(1, 0), bias=False)
        self.bn_m_3 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
        self.t_3 = nn.Conv2d(cout, cout, kernel_size=(1, 3), padding=(0, 1), bias=False)
        self.bn_t_3 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
        self.m_5 = nn.Conv2d(cin, cout, kernel_size=(5, 1), padding=(2, 0), bias=False)
        self.bn_m_5 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
        self.t_5 = nn.Conv2d(cout, cout, kernel_size=(1, 5), padding=(0, 2), bias=False)
        self.bn_t_5 = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)
        self.last = nn.Conv2d(cout, cout, kernel_size=(1, 1), padding=(0, 0), bias=False)
        self.bn_last = nn.BatchNorm2d(cout, momentum=0.01, eps=0.001)

    def forward(self, x):
        x3 = self.relu(self.bn_t_3(self.t_3(self.relu(self.bn_m_3(self.m_3(x))))))
        x5 = self.relu(self.bn_t_5(self.t_5(self.relu(self.bn_m_5(self.m_5(x))))))
        return self.relu(self.bn_last(self.last(x3 + x5)))


class _VisualBlock(nn.Module):
    def __init__(self, cin: int, cout: int, is_down: bool = False):
        super().__init__()
        self.relu = nn.ReLU()
        st = (1, 2, 2) if is_down else (1, 1, 1)
        self.s_3 = nn.Conv3d(cin, cout, kernel_size=(1, 3, 3), stride=st, padding=(0, 1, 1), bias=False)
        self.bn_s_3 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
        self.t_3 = nn.Conv3d(cout, cout, kernel_size=(3, 1, 1), padding=(1, 0, 0), bias=False)
        self.bn_t_3 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
        self.s_5 = nn.Conv3d(cin, cout, kernel_size=(1, 5, 5), stride=st, padding=(0, 2, 2), bias=False)
        self.bn_s_5 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
        self.t_5 = nn.Conv3d(cout, cout, kernel_size=(5, 1, 1), padding=(2, 0, 0), bias=False)
        self.bn_t_5 = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)
        self.last = nn.Conv3d(cout, cout, kernel_size=(1, 1, 1), padding=(0, 0, 0), bias=False)
        self.bn_last = nn.BatchNorm3d(cout, momentum=0.01, eps=0.001)

    def forward(self, x):
        x3 = self.relu(self.bn_t_3(self.t_3(self.relu(self.bn_s_3(self.s_3(x))))))
        x5 = self.relu(self.bn_t_5(self.t_5(self.relu(self.bn_s_5(self.s_5(x))))))
        return self.relu(self.bn_last(self.last(x3 + x5)))


class _VisualEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.block1 = _VisualBlock(1, 32, is_down=True)
        self.pool1 = nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
        self.block2 = _VisualBlock(32, 64)
        self.pool2 = nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
        self.block3 = _VisualBlock(64, 128)
        self.maxpool = nn.AdaptiveMaxPool2d((1, 1))

    def forward(self, x):
        x = self.block3(self.pool2(self.block2(self.pool1(self.block1(x)))))
        x = x.transpose(1, 2)
        b, t, c, w, h = x.shape
        x = self.maxpool(x.reshape(b * t, c, w, h))
        return x.view(b, t, c)


class _AudioEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.block1 = _AudioBlock(1, 32)
        self.pool1 = nn.MaxPool3d(kernel_size=(1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
        self.block2 = _AudioBlock(32, 64)
        self.pool2 = nn.MaxPool3d(kernel_size=(1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
        self.block3 = _AudioBlock(64, 128)

    def forward(self, x):
        x = self.block3(self.pool2(self.block2(self.pool1(self.block1(x)))))
        x = torch.mean(x, dim=2, keepdim=True)
        return x.squeeze(2).transpose(1, 2)


class _BGRU(nn.Module):
    def __init__(self, channel: int):
        super().__init__()
        self.gru_forward = nn.GRU(input_size=channel, hidden_size=channel, num_layers=1, bidirectional=False,
                                  bias=True, batch_first=True)
        self.gru_backward = nn.GRU(input_size=channel, hidden_size=channel, num_layers=1, bidirectional=False,
                                   bias=True, batch_first=True)
        self.gelu = nn.GELU()

    def forward(self, x):
        x, _ = self.gru_forward(x)
        x = torch.flip(self.gelu(x), dims=[1])
        x, _ = self.gru_backward(x)
        return self.gelu(torch.flip(x, dims=[1]))


class _Core(nn.Module):
    def __init__(self):
        super().__init__()
        self.visualEncoder = _VisualEncoder()
        self.audioEncoder = _AudioEncoder()
        self.GRU = _BGRU(128)


class _Head(nn.Module):
    def __init__(self):
        super().__init__()
        self.FC = nn.Linear(128, 2)


class LightASD(nn.Module):
    """Speaking logit per video frame (25 fps) of one face track: ``scores(mfcc [T*4, 13], faces [T, 112, 112])``."""

    def __init__(self):
        super().__init__()
        self.model = _Core()
        self.lossAV = _Head()

    def load(self, path: str) -> "LightASD":
        state = torch.load(path, map_location="cpu", weights_only=True)
        own = self.state_dict()
        got = {k.replace("module.", ""): v for k, v in state.items()}
        missing = [k for k in own if k not in got]
        if missing:
            raise ValueError(f"Light-ASD weights {path}: missing {missing[:5]}")
        self.load_state_dict({k: got[k] for k in own})
        return self.eval()

    @torch.no_grad()
    def scores(self, audio: torch.Tensor, video: torch.Tensor) -> torch.Tensor:
        """audio [B, 4T, 13] MFCC (100 per second), video [B, T, 112, 112] grey 0-255 -> logits [B * T]."""
        m = self.model
        b, t, w, h = video.shape
        v = ((video.view(b, 1, t, w, h) / 255.0) - 0.4161) / 0.1688
        ev = m.visualEncoder(v)
        ea = m.audioEncoder(audio.unsqueeze(1).transpose(2, 3))
        x = torch.reshape(m.GRU(ea + ev), (-1, 128))
        return self.lossAV.FC(x)[:, 1]
