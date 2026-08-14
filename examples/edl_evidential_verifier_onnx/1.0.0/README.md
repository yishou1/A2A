# EDL Evidential Verifier ONNX

Native ONNX package for the EDL belief head.

Feature order: confidence, w/640, h/640, cx/640, cy/640, damage_score.

Orchestration (detection list → features → verified filter) remains in
`edl_evidential_verifier` (`python_http_service`).
