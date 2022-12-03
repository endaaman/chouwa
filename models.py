import re
import torch
import timm
from torch import nn
from torchvision import transforms, models


class EffNet(nn.Module):
    def __init__(self, name='b0', num_classes=1):
        super().__init__()
        self.num_classes = num_classes
        self.m = timm.create_model(f'tf_efficientnetv2_{name}', pretrained=True)
        c = self.m.classifier.in_features
        self.m.classifier = nn.Linear(c, num_classes)

    def get_cam_layer(self):
        return self.m.conv_head

    def forward(self, x):
        x =  self.m(x)
        if self.num_classes > 1:
            x = torch.softmax(x, dim=1)
        else:
            x = torch.sigmoid(x)
        return x


class VGG(nn.Module):
    def __init__(self, name, num_classes=1, pretrained=True):
        super().__init__()
        self.num_classes = num_classes
        vgg = getattr(models, name)
        if not re.match(r'^vgg', name):
            raise Exception(f'At least starting with "vgg": {name}')
        if not vgg:
            raise Exception(f'Invalid model name: {name}')

        base = vgg(pretrained=pretrained)
        self.convs = base.features
        self.avgpool = base.avgpool
        self.classifier = nn.Sequential(
            nn.Linear(512 * 7 * 7, 4096),
            nn.ReLU(True),
            nn.Dropout(),
            nn.Linear(4096, 4096),
            nn.ReLU(True),
            nn.Dropout(),
            nn.Linear(4096, self.num_classes),
        )

    def forward(self, x):
        x = self.convs(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.classifier(x)
        if self.num_classes > 1:
            x = torch.softmax(x, dim=1)
        else:
            x = torch.sigmoid(x)
        # x = torch.where(torch.isnan(x), torch.zeros_like(x), x)
        return x


def create_model(name, num_classes):
    if re.match(r'^vgg', name):
        return VGG(name=name, num_classes=num_classes)

    match = re.match(r'^eff_(b[0-4])$', name)
    if match:
        return EffNet(name=match[1], num_classes=num_classes)

    raise ValueError(f'Invalid name: {name}')


available_models = \
    [f'eff_b{i}' for i in range(4)] + \
    [f'vgg{i}' for i in [11, 13, 16, 19]] + \
    [f'vgg{i}_bn' for i in [11, 13, 16, 19]]


if __name__ == '__main__':
    # m = EffNet('b0')
    m = create_model('vgg16', 3)
    x = torch.rand([2, 3, 512, 512])
    y = m(x)
    # loss = CrossEntropyLoss()
    # print('y', y, y.shape, 'loss', loss(y, torch.LongTensor([1, 1])))
