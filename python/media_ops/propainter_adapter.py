"""ProPainter 官方 RAFT 权重与 pytorchcv 流式模型之间的适配。"""
from __future__ import annotations

import re


def normalize_raft_state(checkpoint: dict) -> dict:
    """将官方 raft-things 的模块名映射到 pytorchcv RAFT。"""
    state = checkpoint.get("state_dict") or checkpoint
    if not isinstance(state, dict):
        raise ValueError("RAFT checkpoint does not contain a state dict")
    normalized = {}
    for raw_key, value in state.items():
        key = raw_key.removeprefix("module.")
        direct = {
            "fnet.conv1": "fnet.features.init_block.conv",
            "fnet.conv2": "fnet.features.final_block",
            "cnet.norm1": "cnet.features.init_block.bn",
            "cnet.conv1": "cnet.features.init_block.conv",
            "cnet.conv2": "cnet.features.final_block",
            "update_block.encoder.convc1": "update_block.encoder.conv_corr.conv_list.conv1.conv",
            "update_block.encoder.convc2": "update_block.encoder.conv_corr.conv_list.conv2.conv",
            "update_block.encoder.convf1": "update_block.encoder.conv_flow.conv_list.conv1.conv",
            "update_block.encoder.convf2": "update_block.encoder.conv_flow.conv_list.conv2.conv",
            "update_block.encoder.conv": "update_block.encoder.conv_out.conv",
            "update_block.flow_head.conv1": "update_block.flow_head.conv1.conv",
            "update_block.mask.0": "update_block.mask.conv1.conv",
            "update_block.mask.2": "update_block.mask.conv2",
        }
        matched = False
        for source, target in direct.items():
            prefix = source + "."
            if key.startswith(prefix):
                normalized[target + key[len(source):]] = value
                matched = True
                break
        if matched:
            continue

        stage = re.fullmatch(r"(fnet|cnet)\.layer([123])\.(\d+)\.(.+)", key)
        if stage:
            family, number, unit, rest = stage.groups()
            unit = int(unit) + 1
            if rest.startswith("downsample."):
                part, field = rest.split(".", 1)
                del part
                kind, parameter = field.split(".", 1)
                if family == "fnet" or kind == "0":
                    target = f"{family}.features.stage{number}.unit1.identity_conv.conv.{parameter}"
                else:
                    target = f"{family}.features.stage{number}.unit1.identity_conv.bn.{parameter}"
            else:
                name, parameter = rest.split(".", 1)
                if name == "norm3":
                    target = f"{family}.features.stage{number}.unit1.identity_conv.bn.{parameter}"
                else:
                    layer = {"conv1": "conv1.conv", "conv2": "conv2.conv", "norm1": "conv1.bn", "norm2": "conv2.bn"}[name]
                    target = f"{family}.features.stage{number}.unit{unit}.body.{layer}.{parameter}"
            normalized[target] = value
            continue

        gru = re.fullmatch(r"update_block\.gru\.(conv([zrq])([12]))\.(.+)", key)
        if gru:
            _whole, letter, index, parameter = gru.groups()
            branch = "horizontal_gru" if index == "1" else "vertical_gru"
            name = {"z": "conv_z", "r": "conv_r", "q": "conv_q"}[letter]
            normalized[f"update_block.gru.{branch}.{name}.conv.{parameter}"] = value
            continue
        normalized[key] = value
    return normalized


def load_raft(model_path: str, use_cuda: bool = False):
    """严格加载官方 RAFT，返回可直接传给流式接口的模型对象。"""
    import torch
    from pytorchcv.models.raft import raft_things

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    model = raft_things(in_normalize=False, iters=20)
    model.load_state_dict(normalize_raft_state(checkpoint), strict=True)
    model.eval()
    if use_cuda:
        model = model.cuda()
    return model


def normalize_flow_state(checkpoint: dict) -> dict:
    """将官方 recurrent_flow_completion 键名映射到 pytorchcv PPRecurrentFlowComplete。"""
    state = checkpoint.get("state_dict") or checkpoint
    if not isinstance(state, dict):
        raise ValueError("Flow completion checkpoint does not contain a state dict")
    direct = {
        "downsample.0": "hg.down_seq.down1.conv.conv",
        "decoder2.0": "hg.up_seq.up3.conv1.conv",
        "decoder2.2.conv": "hg.up_seq.up3.conv2.conv",
        "decoder1.0": "hg.up_seq.up2.conv1.conv",
        "decoder1.2.conv": "hg.up_seq.up2.conv2.conv",
        "upsample.0": "hg.up_seq.up1.conv1.conv",
        "upsample.2.conv": "hg.up_seq.up1.conv2.conv",
        "mid_dilation.0": "hg.skip_seq.skip4.mid_dilation.conv1.conv",
        "mid_dilation.2": "hg.skip_seq.skip4.mid_dilation.conv2.conv",
        "mid_dilation.4": "hg.skip_seq.skip4.mid_dilation.conv3.conv",
        "edgeDetector.projection.0": "edgeDetector.proj.conv",
        "edgeDetector.mid_layer_1.0": "edgeDetector.res_unit.body.conv1.conv",
        "edgeDetector.mid_layer_2.0": "edgeDetector.res_unit.body.conv2.conv",
        "edgeDetector.out_layer": "edgeDetector.out_conv",
    }
    normalized = {}
    for raw_key, value in state.items():
        key = raw_key.removeprefix("module.")
        matched = False
        for source, target in direct.items():
            if key.startswith(source + "."):
                normalized[target + key[len(source):]] = value
                matched = True
                break
        if matched:
            continue

        encoder = re.fullmatch(r"encoder([12])\.([02])\.conv([12])\.0\.(.+)", key)
        if encoder:
            stage, block, conv, parameter = encoder.groups()
            target = f"hg.down_seq.down{int(stage) + 1}.block{int(block) // 2 + 1}.conv{conv}.conv.{parameter}"
            normalized[target] = value
            continue

        deform = re.fullmatch(r"feat_prop_module\.deform_align\.(backward_|forward_)\.(.+)", key)
        if deform:
            branch, rest = deform.groups()
            if rest in {"weight", "bias"}:
                target = f"hg.skip_seq.skip4.feat_prop_module.deform_align.{branch}.deform_conv.{rest}"
            else:
                offset, parameter = rest.removeprefix("conv_offset.").split(".", 1)
                target = f"hg.skip_seq.skip4.feat_prop_module.deform_align.{branch}.conv_offset.conv{int(offset) // 2 + 1}.conv.{parameter}"
            normalized[target] = value
            continue

        backbone = re.fullmatch(r"feat_prop_module\.backbone\.(backward_|forward_)\.([02])\.(.+)", key)
        if backbone:
            branch, index, parameter = backbone.groups()
            target = f"hg.skip_seq.skip4.feat_prop_module.backbone.{branch}.conv{int(index) // 2 + 1}.conv.{parameter}"
            normalized[target] = value
            continue
        if key.startswith("feat_prop_module."):
            normalized[f"hg.skip_seq.skip4.{key}"] = value
        else:
            normalized[key] = value
    return normalized


def load_flow_completion(model_path: str, use_cuda: bool = False):
    """严格加载官方 recurrent_flow_completion 权重。"""
    import torch
    from pytorchcv.models.propainter_rfc import propainter_rfc

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    model = propainter_rfc()
    model.load_state_dict(normalize_flow_state(checkpoint), strict=True)
    model.eval()
    if use_cuda:
        model = model.cuda()
    return model


def normalize_propainter_state(checkpoint: dict) -> dict:
    """将官方 ProPainter 键名映射到 pytorchcv 的同名网络结构。"""
    state = checkpoint.get("state_dict") or checkpoint
    if not isinstance(state, dict):
        raise ValueError("ProPainter checkpoint does not contain a state dict")
    direct = {
        "decoder.0.conv": "decoder.unit1.conv1.conv",
        "decoder.2": "decoder.unit1.conv2.conv",
        "decoder.4.conv": "decoder.unit2.conv1.conv",
        "decoder.6": "decoder.unit2.conv2.conv",
    }
    normalized = {}
    for raw_key, value in state.items():
        key = raw_key.removeprefix("module.")
        matched = False
        for source, target in direct.items():
            if key.startswith(source + "."):
                normalized[target + key[len(source):]] = value
                matched = True
                break
        if matched:
            continue

        encoder = re.fullmatch(r"encoder\.layers\.(\d+)\.(.+)", key)
        if encoder:
            index, parameter = encoder.groups()
            normalized[f"encoder.layers.{int(index) // 2}.conv.{parameter}"] = value
            continue

        deform = re.fullmatch(r"feat_prop_module\.deform_align\.(backward_1|forward_1)\.(.+)", key)
        if deform:
            branch, rest = deform.groups()
            if rest in {"weight", "bias"}:
                target = f"feat_prop_module.deform_align.{branch}.deform_conv.{rest}"
            else:
                offset, parameter = rest.removeprefix("conv_offset.").split(".", 1)
                target = f"feat_prop_module.deform_align.{branch}.conv_offset.conv{int(offset) // 2 + 1}.conv.{parameter}"
            normalized[target] = value
            continue

        backbone = re.fullmatch(r"feat_prop_module\.backbone\.(backward_1|forward_1)\.([02])\.(.+)", key)
        if backbone:
            branch, index, parameter = backbone.groups()
            normalized[f"feat_prop_module.backbone.{branch}.conv{int(index) // 2 + 1}.conv.{parameter}"] = value
            continue

        fuse = re.fullmatch(r"feat_prop_module\.fuse\.([02])\.(.+)", key)
        if fuse:
            index, parameter = fuse.groups()
            normalized[f"feat_prop_module.fuse.conv{int(index) // 2 + 1}.conv.{parameter}"] = value
        else:
            normalized[key] = value
    return normalized


def load_propainter(model_path: str, use_cuda: bool = False):
    """严格加载官方 ProPainter 权重。"""
    import torch
    from pytorchcv.models.propainter import propainter

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    model = propainter()
    model.load_state_dict(normalize_propainter_state(checkpoint), strict=True)
    model.eval()
    if use_cuda:
        model = model.cuda()
    return model
