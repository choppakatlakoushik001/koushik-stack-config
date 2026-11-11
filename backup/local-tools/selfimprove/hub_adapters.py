#!/usr/bin/env python3
"""Multi-hub source adapters for weekly coder model monitoring.

Supports 20 hubs:
Tier 1: Hugging Face, Ollama library, LM Studio community, ModelScope, Kaggle Models,
        GitHub releases, Jan hub, GPT4All models list, Llamafile releases, WiseModel.
Tier 2: Civitai, PyTorch Hub, TensorFlow Hub, ONNX Model Zoo, Ultralytics, OpenMMLab,
        Qualcomm AI Hub, Apple Core ML, BioNeMo, Roboflow.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# Parameter count regex helper
PARAM_RE = re.compile(r'(?:^|[^\w])(\d+(?:\.\d+)?)\s*[bB](?:[^\w]|$)')


def parse_params(name: str) -> float | None:
    m = PARAM_RE.search(name)
    if m:
        return float(m.group(1)) * 1e9
    return None


def estimate_q4_km_bytes(params: float) -> int:
    return int(params * 4.85 / 8)


def fits_6gb(bytes_val: int) -> bool:
    return bytes_val <= 6_000_000_000


def normalize_canonical_key(name: str) -> str:
    """Normalize model name for cross-hub deduplication."""
    clean = name.split('/')[-1].replace(':', '-')
    clean = re.sub(r'[-_.]?(instruct|gguf|q4_k_m|q4|chat).*$', '', clean, flags=re.I)
    return re.sub(r'[^a-z0-9]', '', clean.lower())


class BaseHubAdapter:
    name: str = 'base'
    display_name: str = 'Base'
    tier: int = 1
    is_public_api: bool = True
    skipped_reason: str | None = None

    def fetch(self, timeout: float = 10.0) -> Any:
        raise NotImplementedError

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        raise NotImplementedError

    def scan(self, timeout: float = 10.0, max_age_days: int = 7, now: float = None, fixture_data: Any = None) -> dict:
        if not self.is_public_api:
            return {
                'hub': self.name,
                'display_name': self.display_name,
                'tier': self.tier,
                'checked': False,
                'hits': [],
                'skipped_reason': self.skipped_reason,
                'error': None,
            }
        try:
            if fixture_data is not None:
                raw_data = fixture_data
            else:
                raw_data = self.fetch(timeout=timeout)
            hits = self.parse(raw_data, max_age_days=max_age_days, now=now)
            return {
                'hub': self.name,
                'display_name': self.display_name,
                'tier': self.tier,
                'checked': True,
                'hits': hits,
                'skipped_reason': None,
                'error': None,
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {
                    'hub': self.name,
                    'display_name': self.display_name,
                    'tier': self.tier,
                    'checked': False,
                    'hits': [],
                    'skipped_reason': 'source unavailable',
                    'error': None,
                }
            return {
                'hub': self.name,
                'display_name': self.display_name,
                'tier': self.tier,
                'checked': True,
                'hits': [],
                'skipped_reason': None,
                'error': str(exc),
            }
        except Exception as exc:
            if '404' in str(exc) or 'source unavailable' in str(exc).lower():
                return {
                    'hub': self.name,
                    'display_name': self.display_name,
                    'tier': self.tier,
                    'checked': False,
                    'hits': [],
                    'skipped_reason': 'source unavailable',
                    'error': None,
                }
            return {
                'hub': self.name,
                'display_name': self.display_name,
                'tier': self.tier,
                'checked': True,
                'hits': [],
                'skipped_reason': None,
                'error': str(exc),
            }


# --- Tier 1 Adapters ---

class HuggingFaceAdapter(BaseHubAdapter):
    name = 'huggingface'
    display_name = 'Hugging Face Hub'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://huggingface.co/api/models?search=gguf&blobs=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        now = time.time() if now is None else now
        items = raw_data if isinstance(raw_data, list) else []
        results = []
        for m in items:
            repo_id = m.get('id', '')
            repo_lower = repo_id.lower()
            if not ('coder' in repo_lower or 'code' in repo_lower or 'instruct' in repo_lower):
                continue
            last_mod = m.get('lastModified')
            if last_mod:
                try:
                    ts = dt.datetime.fromisoformat(last_mod.replace('Z', '+00:00')).timestamp()
                    if (now - ts) > (max_age_days * 86400):
                        continue
                except Exception:
                    pass
            params = parse_params(repo_id) or 3e9
            est_size = estimate_q4_km_bytes(params)
            results.append({
                'hub': self.name,
                'repo': repo_id,
                'name': repo_id.split('/')[-1],
                'params': params,
                'size_bytes': est_size,
                'fits': fits_6gb(est_size),
                'is_gguf': True,
                'raw': m,
            })
        return results


class OllamaAdapter(BaseHubAdapter):
    name = 'ollama'
    display_name = 'Ollama Model Library'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://ollama.com/library"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode('utf-8')
            try:
                return json.loads(content)
            except Exception:
                return content

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        if raw_data is None:
            return []

        # If string, try JSON first; if that fails, parse HTML
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except Exception:
                found_names = set(re.findall(r'href=["\'](?:/library/)?([a-zA-Z0-9_.:-]+)["\']', raw_data))
                if not found_names:
                    found_names = set(re.findall(r'/library/([a-zA-Z0-9_.:-]+)', raw_data))
                results = []
                for name in sorted(found_names):
                    name_lower = name.lower()
                    if any(k in name_lower for k in ('coder', 'code', 'qwen', 'deepseek', 'starcoder')):
                        params = parse_params(name) or 7e9
                        size = estimate_q4_km_bytes(params)
                        results.append({
                            'hub': self.name,
                            'repo': f"ollama/{name}",
                            'name': name,
                            'params': params,
                            'size_bytes': size,
                            'fits': fits_6gb(size),
                            'is_gguf': True,
                            'raw': {'name': name, 'source': 'html'},
                        })
                return results

        items = []
        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict):
            items = raw_data.get('models') or []

        results = []
        for m in items:
            if isinstance(m, str):
                name = m
                raw_item = {'name': m}
                size_val = None
            elif isinstance(m, dict):
                name = m.get('name', '')
                raw_item = m
                size_val = m.get('size')
            else:
                continue

            name_lower = name.lower()
            if any(k in name_lower for k in ('coder', 'code', 'qwen', 'deepseek', 'starcoder')):
                params = parse_params(name) or 7e9
                size = size_val or estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': f"ollama/{name}",
                    'name': name,
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': True,
                    'raw': raw_item,
                })
        return results


class LMStudioAdapter(BaseHubAdapter):
    name = 'lmstudio'
    display_name = 'LM Studio Community Hub'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://huggingface.co/api/models?author=lmstudio-community&search=gguf&blobs=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data if isinstance(raw_data, list) else []
        results = []
        for m in items:
            repo_id = m.get('id', '')
            repo_lower = repo_id.lower()
            if 'coder' in repo_lower or 'code' in repo_lower:
                params = parse_params(repo_id) or 7e9
                est_size = estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': repo_id,
                    'name': repo_id.split('/')[-1],
                    'params': params,
                    'size_bytes': est_size,
                    'fits': fits_6gb(est_size),
                    'is_gguf': True,
                    'raw': m,
                })
        return results


class ModelScopeAdapter(BaseHubAdapter):
    name = 'modelscope'
    display_name = 'ModelScope'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        urls = [
            "https://www.modelscope.cn/api/v1/models?Target=code&PageSize=20",
            "https://modelscope.cn/api/v1/models?Target=code&PageSize=20",
        ]
        last_exc = None
        for url in urls:
            req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404:
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                raise
        if last_exc:
            raise last_exc

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        models = []
        if isinstance(raw_data, dict):
            data_val = raw_data.get('data')
            if isinstance(data_val, dict):
                models = data_val.get('Model') or []
            elif isinstance(data_val, list):
                models = data_val
        if not isinstance(models, list):
            models = []
        results = []
        for m in models:
            if not isinstance(m, dict):
                continue
            name = m.get('Name', '')
            name_lower = name.lower()
            if 'code' in name_lower or 'coder' in name_lower or 'qwen' in name_lower:
                params = parse_params(name) or 7e9
                est_size = estimate_q4_km_bytes(params)
                tags = [t.lower() for t in m.get('Tags', []) if isinstance(t, str)]
                is_gguf = 'gguf' in name_lower or 'gguf' in tags
                results.append({
                    'hub': self.name,
                    'repo': f"modelscope/{name}",
                    'name': name,
                    'params': params,
                    'size_bytes': est_size,
                    'fits': fits_6gb(est_size),
                    'is_gguf': is_gguf,
                    'raw': m,
                })
        return results


class KaggleModelsAdapter(BaseHubAdapter):
    name = 'kaggle'
    display_name = 'Kaggle Models'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://www.kaggle.com/api/v1/models/list"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        models = raw_data.get('models', []) if isinstance(raw_data, dict) else []
        results = []
        for m in models:
            title = m.get('title', '')
            if 'code' in title.lower() or 'coder' in title.lower():
                params = parse_params(title) or 7e9
                size = estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': f"kaggle/{m.get('owner', 'kaggle')}/{title}",
                    'name': title,
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': m.get('framework') == 'GGUF' or 'gguf' in title.lower(),
                    'raw': m,
                })
        return results


class GitHubReleasesAdapter(BaseHubAdapter):
    name = 'github'
    display_name = 'GitHub'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://api.github.com/search/repositories?q=gguf+coder&sort=updated"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0', 'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data.get('items', []) if isinstance(raw_data, dict) else []
        results = []
        for item in items:
            name = item.get('full_name', '')
            desc = item.get('description', '') or ''
            if 'coder' in name.lower() or 'coder' in desc.lower() or 'gguf' in name.lower():
                params = parse_params(name) or parse_params(desc) or 7e9
                size = estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': f"github/{name}",
                    'name': name.split('/')[-1],
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': True,
                    'raw': item,
                })
        return results


class JanHubAdapter(BaseHubAdapter):
    name = 'jan'
    display_name = 'Jan Model Hub'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        urls = [
            "https://raw.githubusercontent.com/janhq/models/main/models.json",
            "https://raw.githubusercontent.com/janhq/models/master/models.json",
        ]
        last_exc = None
        for url in urls:
            req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404:
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                raise
        if last_exc:
            raise last_exc

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('data') or []
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            items = []
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = item.get('id', '')
            if 'coder' in ident.lower() or 'code' in ident.lower():
                params = parse_params(ident) or 7e9
                size = item.get('size') or estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': item.get('source') or f"jan/{ident}",
                    'name': ident,
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': str(item.get('format', '')).lower() == 'gguf',
                    'raw': item,
                })
        return results


class GPT4AllAdapter(BaseHubAdapter):
    name = 'gpt4all'
    display_name = 'GPT4All Model Explorer'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://raw.githubusercontent.com/nomic-ai/gpt4all/main/gpt4all-chat/metadata/models3.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data.get('models', []) if isinstance(raw_data, dict) else []
        results = []
        for item in items:
            name = item.get('name', '')
            filename = item.get('filename', '')
            if 'coder' in name.lower() or 'coder' in filename.lower() or item.get('type') == 'coder':
                params = parse_params(filename) or parse_params(name) or 7e9
                size = item.get('filesize') or estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': f"gpt4all/{filename.replace('.gguf', '')}",
                    'name': name,
                    'file': filename,
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': True,
                    'raw': item,
                })
        return results


class LlamafileAdapter(BaseHubAdapter):
    name = 'llamafile'
    display_name = 'Llamafile Releases Repository'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://api.github.com/repos/Mozilla-Ocho/llamafile/releases"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0', 'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        releases = raw_data if isinstance(raw_data, list) else []
        results = []
        for rel in releases:
            for asset in rel.get('assets', []):
                aname = asset.get('name', '')
                if aname.endswith('.llamafile') and ('coder' in aname.lower() or 'code' in aname.lower()):
                    params = parse_params(aname) or 3e9
                    size = asset.get('size') or estimate_q4_km_bytes(params)
                    results.append({
                        'hub': self.name,
                        'repo': f"llamafile/{aname}",
                        'name': aname,
                        'params': params,
                        'size_bytes': size,
                        'fits': fits_6gb(size),
                        'is_gguf': True,  # Llamafile embeds GGUF weights
                        'raw': asset,
                    })
        return results


class WiseModelAdapter(BaseHubAdapter):
    name = 'wisemodel'
    display_name = 'WiseModel'
    tier = 1

    def fetch(self, timeout: float = 10.0) -> Any:
        urls = [
            "https://wisemodel.cn/api/v1/models?search=coder",
            "https://www.wisemodel.cn/api/v1/models?search=coder",
        ]
        last_exc = None
        for url in urls:
            req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404:
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                raise
        if last_exc:
            raise last_exc

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        models = []
        if isinstance(raw_data, dict):
            data_val = raw_data.get('data')
            if isinstance(data_val, dict):
                models = data_val.get('models') or []
            elif isinstance(data_val, list):
                models = data_val
        if not isinstance(models, list):
            models = []
        results = []
        for m in models:
            if not isinstance(m, dict):
                continue
            name = m.get('name', '')
            if 'coder' in name.lower() or 'code' in name.lower():
                params = parse_params(name) or 7e9
                size = estimate_q4_km_bytes(params)
                results.append({
                    'hub': self.name,
                    'repo': f"wisemodel/{name}",
                    'name': name,
                    'params': params,
                    'size_bytes': size,
                    'fits': fits_6gb(size),
                    'is_gguf': str(m.get('format', '')).lower() == 'gguf' or 'gguf' in name.lower(),
                    'raw': m,
                })
        return results


# --- Tier 2 Adapters (Keyword query "code", "coder", record hits, never download) ---

class CivitaiAdapter(BaseHubAdapter):
    name = 'civitai'
    display_name = 'Civitai'
    tier = 2

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://civitai.com/api/v1/models?types=LLM&query=coder"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data.get('items', []) if isinstance(raw_data, dict) else []
        hits = []
        for item in items:
            name = item.get('name', '')
            if 'code' in name.lower() or 'coder' in name.lower():
                hits.append({
                    'hub': self.name,
                    'name': name,
                    'id': str(item.get('id', '')),
                    'type': item.get('type', 'LLM'),
                })
        return hits


class PyTorchHubAdapter(BaseHubAdapter):
    name = 'pytorch_hub'
    display_name = 'PyTorch Hub'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (GitHub repo-based catalog only)'


class TensorFlowHubAdapter(BaseHubAdapter):
    name = 'tfhub'
    display_name = 'TensorFlow Hub / Kaggle Models'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (merged into Kaggle Models)'


class ONNXModelZooAdapter(BaseHubAdapter):
    name = 'onnx_model_zoo'
    display_name = 'ONNX Model Zoo'
    tier = 2

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://api.github.com/repos/onnx/models/contents"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0', 'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data if isinstance(raw_data, list) else []
        hits = []
        for item in items:
            name = item.get('name', '')
            if 'code' in name.lower() or 'coder' in name.lower():
                hits.append({'hub': self.name, 'name': name, 'path': item.get('path', '')})
        return hits


class UltralyticsAdapter(BaseHubAdapter):
    name = 'ultralytics'
    display_name = 'Ultralytics Hub'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (vision YOLO models only)'


class OpenMMLabAdapter(BaseHubAdapter):
    name = 'openmmlab'
    display_name = 'OpenMMLab Model Zoo'
    tier = 2

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://api.github.com/users/open-mmlab/repos"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0', 'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data if isinstance(raw_data, list) else []
        hits = []
        for repo in items:
            name = repo.get('name', '')
            desc = repo.get('description', '') or ''
            if 'code' in name.lower() or 'coder' in desc.lower():
                hits.append({'hub': self.name, 'name': name, 'description': desc})
        return hits


class QualcommAIHubAdapter(BaseHubAdapter):
    name = 'qualcomm_ai_hub'
    display_name = 'Qualcomm AI Hub'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (requires Qualcomm account/SDK)'


class AppleCoreMLAdapter(BaseHubAdapter):
    name = 'apple_coreml'
    display_name = 'Apple Core ML Models Repository'
    tier = 2

    def fetch(self, timeout: float = 10.0) -> Any:
        url = "https://huggingface.co/api/models?author=apple&search=code"
        req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def parse(self, raw_data: Any, max_age_days: int = 7, now: float = None) -> list[dict]:
        items = raw_data if isinstance(raw_data, list) else []
        hits = []
        for m in items:
            ident = m.get('id', '')
            if 'code' in ident.lower() or 'coder' in ident.lower():
                hits.append({'hub': self.name, 'name': ident})
        return hits


class BioNeMoAdapter(BaseHubAdapter):
    name = 'bionemo'
    display_name = 'BioNeMo Model Registry'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (requires NGC API key)'


class RoboflowUniverseAdapter(BaseHubAdapter):
    name = 'roboflow'
    display_name = 'Roboflow Universe'
    tier = 2
    is_public_api = False
    skipped_reason = 'skipped: no public API (vision only, requires API key)'


ALL_ADAPTERS: list[BaseHubAdapter] = [
    # Tier 1
    HuggingFaceAdapter(),
    OllamaAdapter(),
    LMStudioAdapter(),
    ModelScopeAdapter(),
    KaggleModelsAdapter(),
    GitHubReleasesAdapter(),
    JanHubAdapter(),
    GPT4AllAdapter(),
    LlamafileAdapter(),
    WiseModelAdapter(),
    # Tier 2
    CivitaiAdapter(),
    PyTorchHubAdapter(),
    TensorFlowHubAdapter(),
    ONNXModelZooAdapter(),
    UltralyticsAdapter(),
    OpenMMLabAdapter(),
    QualcommAIHubAdapter(),
    AppleCoreMLAdapter(),
    BioNeMoAdapter(),
    RoboflowUniverseAdapter(),
]


def deduplicate_candidates(raw_candidates: list[dict]) -> list[dict]:
    """Deduplicate candidates seen across multiple hubs, consolidating cross-hub sightings."""
    by_canonical: dict[str, dict] = {}

    for cand in raw_candidates:
        canon_key = normalize_canonical_key(cand['name'])
        if not canon_key:
            canon_key = cand['repo']

        if canon_key not in by_canonical:
            by_canonical[canon_key] = {
                'canonical_id': canon_key,
                'repo': cand['repo'],
                'name': cand['name'],
                'primary_hub': cand['hub'],
                'hubs_seen': [cand['hub']],
                'params': cand.get('params'),
                'size_bytes': cand.get('size_bytes', 0),
                'fits': cand.get('fits', False),
                'is_gguf': cand.get('is_gguf', False),
                'raw': cand.get('raw'),
            }
        else:
            entry = by_canonical[canon_key]
            if cand['hub'] not in entry['hubs_seen']:
                entry['hubs_seen'].append(cand['hub'])
            # If current entry is not GGUF but this one is, upgrade
            if not entry['is_gguf'] and cand.get('is_gguf'):
                entry['is_gguf'] = True
                entry['primary_hub'] = cand['hub']
                entry['repo'] = cand['repo']
                entry['name'] = cand['name']

    return list(by_canonical.values())


def scan_all_hubs(
    fetch: bool = True,
    fixtures: dict | None = None,
    per_source_timeout: float = 10.0,
    total_scan_cap: float = 120.0,
    max_age_days: int = 7,
    now: float = None,
) -> dict:
    """Run read-only check across all 20 hubs with timeout caps and error isolation."""
    t_start = time.monotonic()
    sources_summary = []
    tier1_raw_candidates = []
    tier2_hits = []

    for adapter in ALL_ADAPTERS:
        elapsed = time.monotonic() - t_start
        if elapsed >= total_scan_cap:
            sources_summary.append({
                'hub': adapter.name,
                'display_name': adapter.display_name,
                'tier': adapter.tier,
                'checked': False,
                'hits': [],
                'skipped_reason': 'skipped: total scan cap (2 minutes) reached',
                'error': None,
            })
            continue

        fixture_data = fixtures.get(adapter.name) if fixtures else None
        # If fetch is False and no fixture, skip network calls
        if not fetch and fixture_data is None and adapter.is_public_api:
            sources_summary.append({
                'hub': adapter.name,
                'display_name': adapter.display_name,
                'tier': adapter.tier,
                'checked': False,
                'hits': [],
                'skipped_reason': 'fetch disabled',
                'error': None,
            })
            continue

        res = adapter.scan(
            timeout=per_source_timeout,
            max_age_days=max_age_days,
            now=now,
            fixture_data=fixture_data,
        )
        sources_summary.append(res)

        if adapter.tier == 1:
            tier1_raw_candidates.extend(res['hits'])
        else:
            tier2_hits.extend(res['hits'])

    deduped_candidates = deduplicate_candidates(tier1_raw_candidates)

    return {
        'sources': sources_summary,
        'candidates': deduped_candidates,
        'tier2_hits': tier2_hits,
        'total_elapsed_s': round(time.monotonic() - t_start, 2),
    }
