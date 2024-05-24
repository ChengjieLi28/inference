# Copyright 2022-2023 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gc
import logging
import time
import uuid
from threading import Thread
from typing import Iterable, Iterator, List, Optional, Tuple

import torch
from transformers import GenerationConfig, TextIteratorStreamer
from transformers.generation.logits_process import (
    LogitsProcessorList,
    RepetitionPenaltyLogitsProcessor,
    TemperatureLogitsWarper,
    TopKLogitsWarper,
    TopPLogitsWarper,
)

from ....core.scheduler import InferenceRequest
from ....device_utils import empty_cache
from ....types import (
    Completion,
    CompletionChoice,
    CompletionChunk,
    CompletionUsage,
    max_tokens_field,
)

logger = logging.getLogger(__name__)


def is_sentence_complete(output: str):
    """Check whether the output is a complete sentence."""
    end_symbols = (".", "?", "!", "...", "。", "？", "！", "…", '"', "'", "”")
    return output.endswith(end_symbols)


def is_partial_stop(output: str, stop_str: str):
    """Check whether the output contains a partial stop str."""
    for i in range(0, min(len(output), len(stop_str))):
        if stop_str.startswith(output[-i:]):
            return True
    return False


def get_context_length(config) -> int:
    """Get the context length of a model from a huggingface model config."""
    if (
        hasattr(config, "max_sequence_length")
        and config.max_sequence_length is not None
    ):
        max_sequence_length = config.max_sequence_length
    else:
        max_sequence_length = 2048
    if hasattr(config, "seq_length") and config.seq_length is not None:
        seq_length = config.seq_length
    else:
        seq_length = 2048
    if (
        hasattr(config, "max_position_embeddings")
        and config.max_position_embeddings is not None
    ):
        max_position_embeddings = config.max_position_embeddings
    else:
        max_position_embeddings = 2048
    return max(max_sequence_length, seq_length, max_position_embeddings)


def prepare_logits_processor(
    temperature: float, repetition_penalty: float, top_p: float, top_k: int
) -> LogitsProcessorList:
    processor_list = LogitsProcessorList()
    # TemperatureLogitsWarper doesn't accept 0.0, 1.0 makes it a no-op so we skip two cases.
    if temperature >= 1e-5 and temperature != 1.0:
        processor_list.append(TemperatureLogitsWarper(temperature))
    if repetition_penalty > 1.0:
        processor_list.append(RepetitionPenaltyLogitsProcessor(repetition_penalty))
    if 1e-8 <= top_p < 1.0:
        processor_list.append(TopPLogitsWarper(top_p))
    if top_k > 0:
        processor_list.append(TopKLogitsWarper(top_k))
    return processor_list


@torch.inference_mode()
def generate_stream(
    model_uid,
    model,
    tokenizer,
    prompt,
    device,
    generate_config,
    judge_sent_end=False,
) -> Iterator[Tuple[CompletionChunk, CompletionUsage]]:
    context_len = get_context_length(model.config)
    stream_interval = generate_config.get("stream_interval", 2)
    stream = generate_config.get("stream", False)
    stream_options = generate_config.pop("stream_options", None)
    include_usage = (
        stream_options["include_usage"] if isinstance(stream_options, dict) else False
    )

    len_prompt = len(prompt)

    temperature = float(generate_config.get("temperature", 1.0))
    repetition_penalty = float(generate_config.get("repetition_penalty", 1.0))
    top_p = float(generate_config.get("top_p", 1.0))
    top_k = int(generate_config.get("top_k", -1))  # -1 means disable
    max_new_tokens = int(generate_config.get("max_tokens", max_tokens_field.default))
    echo = bool(generate_config.get("echo", False))
    stop_str = generate_config.get("stop", None)
    stop_token_ids = generate_config.get("stop_token_ids", None) or []
    stop_token_ids.append(tokenizer.eos_token_id)

    logits_processor = prepare_logits_processor(
        temperature, repetition_penalty, top_p, top_k
    )

    if ".modeling_qwen." in str(type(model)).lower():
        # TODO: hacky
        input_ids = tokenizer(prompt, allowed_special="all").input_ids
    else:
        input_ids = tokenizer(prompt).input_ids
    output_ids = list(input_ids)

    if model.config.is_encoder_decoder:
        max_src_len = context_len
    else:
        max_src_len = context_len - max_new_tokens - 8
        if max_src_len < 0:
            raise ValueError("Max tokens exceeds model's max length")

    input_ids = input_ids[-max_src_len:]
    input_echo_len = len(input_ids)

    if model.config.is_encoder_decoder:
        encoder_output = model.encoder(
            input_ids=torch.as_tensor([input_ids], device=device)
        )[0]
        start_ids = torch.as_tensor(
            [[model.generation_config.decoder_start_token_id]],
            dtype=torch.int64,
            device=device,
        )

    start = time.time()
    past_key_values = out = None
    sent_interrupt = False
    token = None
    last_output_length = 0
    for i in range(max_new_tokens):
        if i == 0:
            if model.config.is_encoder_decoder:
                out = model.decoder(
                    input_ids=start_ids,
                    encoder_hidden_states=encoder_output,
                    use_cache=True,
                )
                logits = model.lm_head(out[0])
            else:
                out = model(torch.as_tensor([input_ids], device=device), use_cache=True)
                logits = out.logits
            past_key_values = out.past_key_values
        else:
            if model.config.is_encoder_decoder:
                out = model.decoder(
                    input_ids=torch.as_tensor(
                        [[token] if not sent_interrupt else output_ids], device=device
                    ),
                    encoder_hidden_states=encoder_output,
                    use_cache=True,
                    past_key_values=past_key_values if not sent_interrupt else None,
                )
                sent_interrupt = False

                logits = model.lm_head(out[0])
            else:
                out = model(
                    input_ids=torch.as_tensor(
                        [[token] if not sent_interrupt else output_ids], device=device
                    ),
                    use_cache=True,
                    past_key_values=past_key_values if not sent_interrupt else None,
                )
                sent_interrupt = False
                logits = out.logits
            past_key_values = out.past_key_values

        if logits_processor:
            if repetition_penalty > 1.0:
                tmp_output_ids = torch.as_tensor([output_ids], device=logits.device)
            else:
                tmp_output_ids = None
            last_token_logits = logits_processor(tmp_output_ids, logits[:, -1, :])[0]
        else:
            last_token_logits = logits[0, -1, :]

        if device == "mps":
            # Switch to CPU by avoiding some bugs in mps backend.
            last_token_logits = last_token_logits.float().to("cpu")

        if temperature < 1e-5 or top_p < 1e-8:  # greedy
            _, indices = torch.topk(last_token_logits, 2)
            tokens = [int(index) for index in indices.tolist()]
        else:
            probs = torch.softmax(last_token_logits, dim=-1)
            indices = torch.multinomial(probs, num_samples=2)
            tokens = [int(token) for token in indices.tolist()]
        token = tokens[0]
        output_ids.append(token)

        if token in stop_token_ids:
            stopped = True
        else:
            stopped = False

        if i % stream_interval == 0 or i == max_new_tokens - 1 or stopped:
            if echo:
                tmp_output_ids = output_ids
                rfind_start = len_prompt
            else:
                tmp_output_ids = output_ids[input_echo_len:]
                rfind_start = 0

            output = tokenizer.decode(
                tmp_output_ids,
                skip_special_tokens=True,
                spaces_between_special_tokens=False,
                clean_up_tokenization_spaces=True,
            )

            # TODO: For the issue of incomplete sentences interrupting output, apply a patch and others can also modify it to a more elegant way
            if judge_sent_end and stopped and not is_sentence_complete(output):
                if len(tokens) > 1:
                    token = tokens[1]
                    output_ids[-1] = token
                else:
                    output_ids.pop()
                stopped = False
                sent_interrupt = True

            partially_stopped = False
            if stop_str:
                if isinstance(stop_str, str):
                    pos = output.rfind(stop_str, rfind_start)
                    if pos != -1:
                        output = output[:pos]
                        stopped = True
                    else:
                        partially_stopped = is_partial_stop(output, stop_str)
                elif isinstance(stop_str, Iterable):
                    for each_stop in stop_str:
                        pos = output.rfind(each_stop, rfind_start)
                        if pos != -1:
                            output = output[:pos]
                            stopped = True
                            break
                        else:
                            partially_stopped = is_partial_stop(output, each_stop)
                            if partially_stopped:
                                break
                else:
                    raise ValueError("Invalid stop field type.")

            if stream:
                output = output.strip("�")
                tmp_output_length = len(output)
                output = output[last_output_length:]
                last_output_length = tmp_output_length

            # prevent yielding partial stop sequence
            if not partially_stopped:
                completion_choice = CompletionChoice(
                    text=output, index=0, logprobs=None, finish_reason=None
                )
                completion_chunk = CompletionChunk(
                    id=str(uuid.uuid1()),
                    object="text_completion",
                    created=int(time.time()),
                    model=model_uid,
                    choices=[completion_choice],
                )
                completion_usage = CompletionUsage(
                    prompt_tokens=input_echo_len,
                    completion_tokens=i,
                    total_tokens=(input_echo_len + i),
                )

                yield completion_chunk, completion_usage

        if stopped:
            break

    elapsed_time = time.time() - start
    logger.info(f"Average generation speed: {i / elapsed_time:.2f} tokens/s.")

    # finish stream event, which contains finish reason
    if stopped:
        finish_reason = "stop"
    elif i == max_new_tokens - 1:
        finish_reason = "length"
    else:
        finish_reason = None

    if stream:
        completion_choice = CompletionChoice(
            text="", index=0, logprobs=None, finish_reason=finish_reason
        )
    else:
        completion_choice = CompletionChoice(
            text=output, index=0, logprobs=None, finish_reason=finish_reason
        )

    completion_chunk = CompletionChunk(
        id=str(uuid.uuid1()),
        object="text_completion",
        created=int(time.time()),
        model=model_uid,
        choices=[completion_choice],
    )
    completion_usage = CompletionUsage(
        prompt_tokens=input_echo_len,
        completion_tokens=i,
        total_tokens=(input_echo_len + i),
    )

    yield completion_chunk, completion_usage

    if include_usage:
        completion_chunk = CompletionChunk(
            id=str(uuid.uuid1()),
            object="text_completion",
            created=int(time.time()),
            model=model_uid,
            choices=[],
        )
        completion_usage = CompletionUsage(
            prompt_tokens=input_echo_len,
            completion_tokens=i,
            total_tokens=(input_echo_len + i),
        )
        yield completion_chunk, completion_usage

    # clean
    del past_key_values, out
    gc.collect()
    empty_cache()


@torch.inference_mode()
def generate_stream_falcon(
    model_uid,
    model,
    tokenizer,
    prompt,
    device,
    generate_config,
    judge_sent_end=False,
) -> Iterator[Tuple[CompletionChunk, CompletionUsage]]:
    context_len = get_context_length(model.config)
    stream_interval = generate_config.get("stream_interval", 2)
    stream = generate_config.get("stream", False)
    stream_options = generate_config.pop("stream_options", None)
    include_usage = (
        stream_options["include_usage"] if isinstance(stream_options, dict) else False
    )
    len_prompt = len(prompt)

    temperature = float(generate_config.get("temperature", 1.0))
    repetition_penalty = float(generate_config.get("repetition_penalty", 1.0))
    top_p = float(generate_config.get("top_p", 1.0))
    top_k = int(generate_config.get("top_k", 50))  # -1 means disable
    max_new_tokens = int(generate_config.get("max_tokens", max_tokens_field.default))
    echo = bool(generate_config.get("echo", False))
    stop_str = generate_config.get("stop", None)
    stop_token_ids = generate_config.get("stop_token_ids", None) or []
    stop_token_ids.append(tokenizer.eos_token_id)

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    input_ids = inputs["input_ids"]
    attention_mask = inputs["attention_mask"]

    max_src_len = context_len - max_new_tokens - 8

    input_ids = input_ids[-max_src_len:]  # truncate from the left
    attention_mask = attention_mask[-max_src_len:]  # truncate from the left
    input_echo_len = len(input_ids)

    decode_config = dict(skip_special_tokens=True, clean_up_tokenization_spaces=True)
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, **decode_config)

    generation_config = GenerationConfig(
        max_new_tokens=max_new_tokens,
        do_sample=temperature >= 1e-5,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        no_repeat_ngram_size=10,
        top_p=top_p,
        top_k=top_k,
        eos_token_id=stop_token_ids,
    )

    generation_kwargs = dict(
        inputs=input_ids,
        attention_mask=attention_mask,
        streamer=streamer,
        generation_config=generation_config,
    )

    thread = Thread(target=model.generate, kwargs=generation_kwargs)
    thread.start()

    if echo:
        # means keep the prompt
        output = prompt
    else:
        output = ""

    last_output_length = 0
    for i, new_text in enumerate(streamer):
        output += new_text
        if i % stream_interval == 0:
            if echo:
                rfind_start = len_prompt
            else:
                rfind_start = 0

            partially_stopped = False
            if stop_str:
                if isinstance(stop_str, str):
                    pos = output.rfind(stop_str, rfind_start)
                    if pos != -1:
                        output = output[:pos]
                    else:
                        partially_stopped = is_partial_stop(output, stop_str)
                elif isinstance(stop_str, Iterable):
                    for each_stop in stop_str:
                        pos = output.rfind(each_stop, rfind_start)
                        if pos != -1:
                            output = output[:pos]
                            break
                        else:
                            partially_stopped = is_partial_stop(output, each_stop)
                            if partially_stopped:
                                break
                else:
                    raise ValueError("Invalid stop field type.")

            if stream:
                output = output.strip("�")
                tmp_output_length = len(output)
                output = output[last_output_length:]
                last_output_length = tmp_output_length

            # prevent yielding partial stop sequence
            if not partially_stopped:
                completion_choice = CompletionChoice(
                    text=output, index=0, logprobs=None, finish_reason=None
                )
                completion_chunk = CompletionChunk(
                    id=str(uuid.uuid1()),
                    object="text_completion",
                    created=int(time.time()),
                    model=model_uid,
                    choices=[completion_choice],
                )
                completion_usage = CompletionUsage(
                    prompt_tokens=input_echo_len,
                    completion_tokens=i,
                    total_tokens=(input_echo_len + i),
                )

                yield completion_chunk, completion_usage
    output = output.strip()

    # finish stream event, which contains finish reason
    if i == max_new_tokens - 1:
        finish_reason = "length"
    elif partially_stopped:
        finish_reason = None
    else:
        finish_reason = "stop"

    completion_choice = CompletionChoice(
        text=output, index=0, logprobs=None, finish_reason=finish_reason
    )
    completion_chunk = CompletionChunk(
        id=str(uuid.uuid1()),
        object="text_completion",
        created=int(time.time()),
        model=model_uid,
        choices=[completion_choice],
    )
    completion_usage = CompletionUsage(
        prompt_tokens=input_echo_len,
        completion_tokens=i,
        total_tokens=(input_echo_len + i),
    )

    yield completion_chunk, completion_usage

    if include_usage:
        completion_chunk = CompletionChunk(
            id=str(uuid.uuid1()),
            object="text_completion",
            created=int(time.time()),
            model=model_uid,
            choices=[],
        )
        completion_usage = CompletionUsage(
            prompt_tokens=input_echo_len,
            completion_tokens=i,
            total_tokens=(input_echo_len + i),
        )
        yield completion_chunk, completion_usage

    # clean
    gc.collect()
    empty_cache()


def get_token_from_logits(
    req: InferenceRequest, i: int, logits, temperature, repetition_penalty, top_p, top_k
):
    logits_processor = prepare_logits_processor(
        temperature, repetition_penalty, top_p, top_k
    )

    if logits_processor:
        if repetition_penalty > 1.0:
            tmp_output_ids = torch.as_tensor(
                [req.prompt_tokens + req.new_tokens], device=logits.device
            )
        else:
            tmp_output_ids = None
        last_token_logits = logits_processor(tmp_output_ids, logits[i : i + 1, -1, :])[
            0
        ]
    else:
        last_token_logits = logits[i : i + 1, -1, :]

    if temperature < 1e-5 or top_p < 1e-8:  # greedy
        _, indices = torch.topk(last_token_logits, 2)
    else:
        probs = torch.softmax(last_token_logits, dim=-1)
        indices = torch.multinomial(probs, num_samples=2)
    token = indices[0].int().item()
    return token


def _pad_to_max_length(x: List[int], max_len: int, pad: int) -> List[int]:
    assert len(x) <= max_len
    return [pad] * (max_len - len(x)) + x


def pad_seqs_inplace(seqs: List[List[int]], pad: int):
    max_len = max(len(seq) for seq in seqs)
    n = len(seqs)
    i = 0
    while i < n:
        seqs[i] = _pad_to_max_length(seqs[i], max_len, pad)
        i += 1


def get_max_src_len(context_len: int, r: InferenceRequest) -> int:
    max_new_tokens = int(
        r.sanitized_generate_config.get("max_tokens", max_tokens_field.default)
    )
    return context_len - max_new_tokens - 8


def get_completion_chunk(
    output: str, finish_reason: Optional[str], model_uid: str, r: InferenceRequest
):
    completion_choice = CompletionChoice(
        text=output, index=0, logprobs=None, finish_reason=finish_reason
    )
    completion_chunk = CompletionChunk(
        id=str(uuid.uuid1()),
        object="text_completion",
        created=int(time.time()),
        model=model_uid,
        choices=[completion_choice],
    )
    completion_usage = CompletionUsage(
        prompt_tokens=len(r.prompt_tokens),
        completion_tokens=len(r.new_tokens),
        total_tokens=len(r.prompt_tokens) + len(r.new_tokens),
    )
    completion_chunk["usage"] = completion_usage
    return completion_chunk


@torch.inference_mode()
def batch_inference_one_step(
    req_list: List[InferenceRequest],
    model_uid,
    model,
    tokenizer,
    device,
    context_len: int,
):
    # need to judge stopped here,
    # since some requests state may change to stopped due to invalid parameters, e.g. max_src_len
    valid_req_list = [r for r in req_list if not r.stopped]
    if not valid_req_list:
        return
    prompts = [r.full_prompt for r in valid_req_list if r.is_prefill and not r.stopped]
    not_use_kv_cache_in_decode = all(r.kv_cache is None for r in valid_req_list)
    if prompts:
        input_ids: List[List[int]] = tokenizer(prompts, padding=False).input_ids

        prompt_tokens: List[List[int]] = []
        for i, r in enumerate(valid_req_list):
            r.kv_cache = None
            if i < len(input_ids):
                input_id = input_ids[i]
                max_src_len = get_max_src_len(context_len, r)
                r.prompt_tokens = input_id[-max_src_len:]
            prompt_tokens.append(
                r.prompt_tokens if r.is_prefill else r.prompt_tokens + r.new_tokens
            )
        pad_seqs_inplace(prompt_tokens, 0)
        out = model(torch.as_tensor(prompt_tokens, device=device), use_cache=True)
    elif not_use_kv_cache_in_decode:
        decodes: List[List[int]] = []
        for i, r in enumerate(valid_req_list):
            r.kv_cache = None
            decodes.append(r.prompt_tokens + r.new_tokens)
        pad_seqs_inplace(decodes, 0)
        out = model(torch.as_tensor(decodes, device=device), use_cache=True)
    else:
        decode_tokens: List[List[int]] = [[r.new_tokens[-1]] for r in valid_req_list]
        kv_cache = valid_req_list[0].kv_cache
        out = model(
            input_ids=torch.as_tensor(decode_tokens, device=device),
            use_cache=True,
            past_key_values=kv_cache,
        )
    logits = out.logits
    past_key_values = out.past_key_values

    for i, r in enumerate(valid_req_list):
        max_new_tokens = int(
            r.sanitized_generate_config.get("max_tokens", max_tokens_field.default)
        )
        stream_interval = r.sanitized_generate_config.get("stream_interval", 2)
        # TODO: handle stop str
        # stop_str = r.sanitized_generate_config.get("stop", None)
        stop_token_ids = r.sanitized_generate_config.get("stop_token_ids", None) or []
        stop_token_ids.append(tokenizer.eos_token_id)
        temperature = float(r.sanitized_generate_config.get("temperature", 1.0))
        repetition_penalty = float(
            r.sanitized_generate_config.get("repetition_penalty", 1.0)
        )
        top_p = float(r.sanitized_generate_config.get("top_p", 1.0))
        top_k = int(r.sanitized_generate_config.get("top_k", -1))  # -1 means disable

        token = get_token_from_logits(
            r, i, logits, temperature, repetition_penalty, top_p, top_k
        )
        stopped = token in stop_token_ids

        r.kv_cache = past_key_values
        r.is_prefill = False
        r.append_new_token(token)

        if stopped:
            finish_reason = "stop"
        elif len(r.new_tokens) == max_new_tokens:
            finish_reason = "length"
            stopped = True
        else:
            finish_reason = None

        r.stopped = stopped

        if r.stream:
            """
            Note that you can't just decode based on r.new_tokens here,
            which may destroy the integrity of the parsed characters,
            and at the same time is not good at handling some special characters.
            """
            remain_num = len(r.new_tokens) % stream_interval
            if stopped or remain_num == 0:
                output = tokenizer.decode(
                    r.new_tokens,
                    skip_special_tokens=True,
                    spaces_between_special_tokens=False,
                    clean_up_tokenization_spaces=True,
                )
                # this special character is mainly for qwen
                output = output.strip("�")
                output = output[r.last_output_length :]
                r.last_output_length += len(output)

                completion_chunk = get_completion_chunk(
                    output, finish_reason, model_uid, r
                )
                r.completion = [completion_chunk]
            else:  # not stopped and not in stream_interval, just not yield to upstream
                r.completion = []
        else:
            if r.stopped:
                outputs = tokenizer.decode(
                    r.new_tokens[:-1] if finish_reason == "stop" else r.new_tokens,
                    skip_special_tokens=True,
                    spaces_between_special_tokens=False,
                    clean_up_tokenization_spaces=True,
                )

                completion_choice = CompletionChoice(
                    text=outputs, index=0, logprobs=None, finish_reason=finish_reason
                )

                completion_chunk = CompletionChunk(
                    id=str(uuid.uuid1()),
                    object="text_completion",
                    created=int(time.time()),
                    model=model_uid,
                    choices=[completion_choice],
                )
                completion_usage = CompletionUsage(
                    prompt_tokens=len(r.prompt_tokens),
                    completion_tokens=len(r.new_tokens),
                    total_tokens=len(r.prompt_tokens) + len(r.new_tokens),
                )
                completion = Completion(
                    id=completion_chunk["id"],
                    object=completion_chunk["object"],
                    created=completion_chunk["created"],
                    model=completion_chunk["model"],
                    choices=completion_chunk["choices"],
                    usage=completion_usage,
                )
                r.completion = [completion]
