#!/usr/bin/env python3
"""Single-speaker SFT for Qwen3-TTS Base on Apple Silicon (MPS).

The official `finetuning/sft_12hz.py` assumes CUDA: flash-attention 2, an
`Accelerator(mixed_precision="bf16")` and `accelerator.prepare`. None of that
runs on MPS. This is the same training step — same dataset, same loss, same
checkpoint format — driven by hand so it can run on a 24 GB Mac:

  * no accelerate: plain autocast-free bf16 weights + AdamW + manual accumulation
  * `attn_implementation="eager"` instead of flash-attention
  * checkpoints written exactly like the official script (config patched to
    `custom_voice` with one named speaker, speaker_encoder dropped), so the
    result loads with `generate_custom_voice(speaker=<name>)`

Memory is the binding constraint on 24 GB. Full-parameter AdamW needs ~18 GB
(fp32 master + two fp32 moments + grads) and OOMs the MPS allocator; updating
bf16 params directly instead makes a ~2e-5 step vanish into bf16 rounding and
the model degrades. `--lora-rank` sidesteps both: the base model stays frozen
in bf16, and only small fp32 adapters are trained and merged back at save time.

Input JSONL is the output of `prepare_data.py`: every row needs `audio`,
`text`, `ref_audio` and `audio_codes`.

Example:
  python3 tools/voice/train_12hz_mps.py \
    --init-model-path ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-0.6B-Base/snapshots/master \
    --train-jsonl assets/voice/data/amitaro-yofukashi/train_with_codes.jsonl \
    --output-model-path assets/voice/models/psyche-0.6b \
    --speaker-name psyche --device mps
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor" / "qwen3tts-finetuning"

# LoRA goes on the projections that carry the talker's language modelling and the
# code predictor's sub-talker; embeddings and the codec head keep their pretrained shape.
LORA_TARGETS = ("self_attn.", "mlp.", "text_projection.", "code_predictor.")


class LoRALinear:
    """Wraps a frozen Linear with a trainable fp32 low-rank update."""

    @staticmethod
    def build(module_cls):
        import math

        import torch
        from torch import nn

        class _LoRALinear(nn.Module):
            def __init__(self, base, rank: int, alpha: float):
                super().__init__()
                self.base = base
                for p in base.parameters():
                    p.requires_grad = False
                self.rank = rank
                self.scale = alpha / rank if alpha else 2.0
                dev = base.weight.device
                self.lora_a = nn.Parameter(torch.empty(base.in_features, rank, dtype=torch.float32, device=dev))
                self.lora_b = nn.Parameter(torch.zeros(rank, base.out_features, dtype=torch.float32, device=dev))
                nn.init.kaiming_uniform_(self.lora_a, a=math.sqrt(5))

            def forward(self, x):
                out = self.base(x)
                delta = (x.to(torch.float32) @ self.lora_a @ self.lora_b) * self.scale
                return out + delta.to(out.dtype)

            @torch.no_grad()
            def merge(self):
                weight = self.base.weight
                weight.add_((self.lora_a @ self.lora_b).T.to(weight.dtype) * self.scale)

            @torch.no_grad()
            def unmerge(self):
                weight = self.base.weight
                weight.sub_((self.lora_a @ self.lora_b).T.to(weight.dtype) * self.scale)

        return _LoRALinear


def apply_lora(model, rank: int, alpha: float):
    import torch
    from torch import nn

    lora_cls = LoRALinear.build(nn.Linear)
    patched = []
    for name, module in list(model.named_modules()):
        if not any(t in name + "." for t in LORA_TARGETS):
            continue
        for child_name, child in list(module.named_children()):
            if isinstance(child, nn.Linear):
                wrapped = lora_cls(child, rank, alpha)
                setattr(module, child_name, wrapped)
                patched.append((module, child_name, wrapped))
    for p in model.parameters():
        p.requires_grad = False
    trainable = []
    for _, _, wrapped in patched:
        wrapped.lora_a.requires_grad = True
        wrapped.lora_b.requires_grad = True
        trainable += [wrapped.lora_a, wrapped.lora_b]
    print(f"lora: {len(patched)} layers, {sum(p.numel() for p in trainable) / 1e6:.2f}M trainable", flush=True)
    return patched


def merge_lora(patched) -> None:
    for parent, child_name, wrapped in patched:
        wrapped.merge()
        setattr(parent, child_name, wrapped.base)


def unmerge_lora(patched) -> None:
    """Put the adapters back as modules after a checkpoint, so training continues correctly."""
    for parent, child_name, wrapped in patched:
        wrapped.unmerge()
        setattr(parent, child_name, wrapped)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Qwen3-TTS single-speaker SFT on MPS.")
    ap.add_argument("--init-model-path", required=True, type=Path)
    ap.add_argument("--train-jsonl", required=True, type=Path)
    ap.add_argument("--output-model-path", required=True, type=Path)
    ap.add_argument("--speaker-name", default="psyche")
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--num-epochs", type=int, default=3)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float32"])
    ap.add_argument("--master-fp32", action="store_true",
                    help="keep fp32 master weights under bf16 params (what accelerate does)")
    ap.add_argument("--lora-rank", type=int, default=0,
                    help="train fp32 LoRA adapters of this rank instead of full parameters")
    ap.add_argument("--lora-alpha", type=float, default=0.0, help="LoRA scale; default 2x rank")
    ap.add_argument("--limit-steps", type=int, default=0, help="stop after N optimizer steps (smoke test)")
    ap.add_argument("--save-each-epoch", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--log-every", type=int, default=10)
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    import torch
    from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
    from torch.optim import AdamW
    from torch.utils.data import DataLoader
    from transformers import AutoConfig

    sys.path.insert(0, str(VENDOR))
    from dataset import TTSDataset  # noqa: E402  (vendored official code)

    dtype = getattr(torch, args.dtype)
    device = torch.device(args.device)
    print(f"loading {args.init_model_path} on {args.device} ({args.dtype})", flush=True)
    t0 = time.time()
    qwen3tts = Qwen3TTSModel.from_pretrained(
        str(args.init_model_path),
        dtype=dtype,
        low_cpu_mem_usage=True,
        attn_implementation="eager",
    )
    model = qwen3tts.model.to(args.device)
    qwen3tts.device = torch.device(args.device)  # wrapper caches device for its generators
    model.train()
    print(f"loaded in {time.time() - t0:.1f}s", flush=True)

    lora_patches = []
    if args.lora_rank:
        lora_patches = apply_lora(model, args.lora_rank, args.lora_alpha or 2 * args.lora_rank)

    config = AutoConfig.from_pretrained(str(args.init_model_path))
    rows = [json.loads(line) for line in args.train_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    missing = [r["audio"] for r in rows if "audio_codes" not in r]
    if missing:
        print(f"{len(missing)} rows have no audio_codes — run prepare_data.py first", flush=True)
        return 2
    print(f"{len(rows)} training rows", flush=True)

    dataset = TTSDataset(rows, qwen3tts.processor, config)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, collate_fn=dataset.collate_fn)

    # accelerate keeps fp32 master weights under bf16 mixed precision. Updating bf16 params
    # directly makes the ~2e-5 step vanish into bf16 rounding, so mirror that here.
    masters = None
    if lora_patches:
        optimizer = AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.01)
    elif args.master_fp32:
        masters = [torch.nn.Parameter(p.detach().float().clone()) for p in model.parameters()]
        optimizer = AdamW(masters, lr=args.lr, weight_decay=0.01)
        print(f"fp32 master weights: {sum(m.numel() for m in masters) / 1e6:.0f}M params", flush=True)
    else:
        optimizer = AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)

    target_speaker_embedding = None
    step_global = 0
    started = time.time()
    for epoch in range(args.num_epochs):
        for step, batch in enumerate(loader):
            # accelerate moved batches to the device in the official script; here it is explicit.
            batch = {k: v.to(device) if torch.is_tensor(v) else v for k, v in batch.items()}
            input_ids = batch["input_ids"]
            codec_ids = batch["codec_ids"]
            ref_mels = batch["ref_mels"]
            text_embedding_mask = batch["text_embedding_mask"]
            codec_embedding_mask = batch["codec_embedding_mask"]
            attention_mask = batch["attention_mask"]
            codec_0_labels = batch["codec_0_labels"]
            codec_mask = batch["codec_mask"]

            # explicit device/dtype: with LoRA the module's first parameter may be an fp32 adapter,
            # which would make `model.dtype` report float32 and break the bf16 conv stack
            speaker_embedding = model.speaker_encoder(ref_mels.to(device).to(dtype)).detach()
            if target_speaker_embedding is None:
                target_speaker_embedding = speaker_embedding

            input_text_ids = input_ids[:, :, 0]
            input_codec_ids = input_ids[:, :, 1]

            # 0.6B has text_hidden_size 2048 -> talker hidden 1024, so the projection is required.
            # (The official script omits it because it only targets 1.7B, where both are 2048.)
            input_text_embedding = model.talker.text_projection(
                model.talker.model.text_embedding(input_text_ids)
            ) * text_embedding_mask
            input_codec_embedding = model.talker.model.codec_embedding(input_codec_ids) * codec_embedding_mask
            input_codec_embedding[:, 6, :] = speaker_embedding

            input_embeddings = input_text_embedding + input_codec_embedding

            for i in range(1, 16):
                codec_i_embedding = model.talker.code_predictor.get_input_embeddings()[i - 1](codec_ids[:, :, i])
                codec_i_embedding = codec_i_embedding * codec_mask.unsqueeze(-1)
                input_embeddings = input_embeddings + codec_i_embedding

            outputs = model.talker(
                inputs_embeds=input_embeddings[:, :-1, :],
                attention_mask=attention_mask[:, :-1],
                labels=codec_0_labels[:, 1:],
                output_hidden_states=True,
            )

            hidden_states = outputs.hidden_states[0][-1]
            talker_hidden_states = hidden_states[codec_mask[:, :-1]]
            talker_codec_ids = codec_ids[codec_mask]

            sub_talker_logits, sub_talker_loss = model.talker.forward_sub_talker_finetune(
                talker_codec_ids, talker_hidden_states
            )

            loss = (outputs.loss + 0.3 * sub_talker_loss) / args.grad_accum
            loss.backward()

            if (step + 1) % args.grad_accum == 0:
                if masters is not None:
                    for param, master in zip(model.parameters(), masters):
                        master.grad = param.grad.float() if param.grad is not None else None
                    torch.nn.utils.clip_grad_norm_(masters, 1.0)
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)
                    with torch.no_grad():
                        for param, master in zip(model.parameters(), masters):
                            param.copy_(master.to(param.dtype))
                else:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)
                step_global += 1
                if step_global % args.log_every == 0:
                    print(
                        f"epoch {epoch} | opt-step {step_global} | loss {loss.item() * args.grad_accum:.4f} "
                        f"| {(time.time() - started) / 60:.1f} min",
                        flush=True,
                    )
                if args.limit_steps and step_global >= args.limit_steps:
                    print("limit-steps reached", flush=True)
                    break
        if args.limit_steps and step_global >= args.limit_steps:
            break
        if args.save_each_epoch:
            if lora_patches:
                merge_lora(lora_patches)
            save_checkpoint(args, model, target_speaker_embedding, epoch)
            if lora_patches:
                unmerge_lora(lora_patches)

    if lora_patches:
        merge_lora(lora_patches)
    save_checkpoint(args, model, target_speaker_embedding, "final")
    if lora_patches:
        unmerge_lora(lora_patches)
    print(f"done: {step_global} optimizer steps in {(time.time() - started) / 60:.1f} min", flush=True)
    return 0


def save_checkpoint(args, model, target_speaker_embedding, tag) -> None:
    import torch
    from safetensors.torch import save_file

    out_dir = args.output_model_path / f"checkpoint-epoch-{tag}"
    shutil.copytree(str(args.init_model_path), str(out_dir), dirs_exist_ok=True)

    cfg_path = out_dir / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["tts_model_type"] = "custom_voice"
    talker_cfg = cfg.get("talker_config", {})
    talker_cfg["spk_id"] = {args.speaker_name: 3000}
    talker_cfg["spk_is_dialect"] = {args.speaker_name: False}
    cfg["talker_config"] = talker_cfg
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    state = {k: v.detach().to("cpu") for k, v in model.state_dict().items()}
    for k in [k for k in state if k.startswith("speaker_encoder")]:
        del state[k]
    weight = state["talker.model.codec_embedding.weight"]
    weight[3000] = target_speaker_embedding[0].detach().to(weight.device).to(weight.dtype)
    save_file(state, str(out_dir / "model.safetensors"))
    print(f"saved {out_dir}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
