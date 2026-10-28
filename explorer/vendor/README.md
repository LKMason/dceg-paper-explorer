# Browser inference runtime

`transformers.web.min.js` is the Apache-2.0 licensed web distribution of
`@huggingface/transformers` 4.3.1, downloaded from
https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.1/dist/transformers.web.min.js.
Its license is included in `TRANSFORMERS-LICENSE`.

The two bare imports (`onnxruntime-web/webgpu` and `onnxruntime-common`) have
been replaced with the same absolute, pinned ONNX Runtime browser module:
https://cdn.jsdelivr.net/npm/onnxruntime-web@1.31.0-dev.20260914-8d85527a0/dist/ort.webgpu.bundle.min.mjs.
This uses a single Tensor implementation and works in a Blob worker when the
HTML is opened directly from disk, without an import map or application server.

ONNX Runtime, its WebAssembly assets, and the quantized text model are fetched
by the browser only when a custom semantic query or a new paper embedding is first requested. They are
not required for the precomputed demo queries or other explorer features.
