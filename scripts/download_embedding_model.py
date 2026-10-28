#!/usr/bin/env python3
"""Download only model assets; publication data is never read or uploaded."""
from datetime import datetime, timezone
import json

from huggingface_hub import snapshot_download
from embed_publications import MODEL_ID, REVISION, ROOT

target = ROOT / '.cache/embeddinggemma-2'
snapshot_download(repo_id=MODEL_ID, revision=REVISION, local_dir=target,
                  allow_patterns=['*.json', '*.safetensors', '*.model', '*.jinja', 'README.md'])
(target / 'download_provenance.json').write_text(json.dumps({
    'model_id': MODEL_ID, 'revision': REVISION,
    'downloaded_at': datetime.now(timezone.utc).isoformat(),
}, indent=2) + '\n')
print(f'Model ready: {target}', flush=True)
