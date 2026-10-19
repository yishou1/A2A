# Conditional Tabular GAN

This M08 package contains a trained PyTorch conditional GAN generator. The
service generates bounded synthetic feature rows for development and data
augmentation experiments. Every response is explicitly labelled synthetic.

The bundled model was trained only on repository-generated reference data. Its
distribution metric is not evidence of operational realism.

Retrain:

```powershell
python scripts/train_conditional_tabular_gan.py
```

Run strict acceptance:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group m08-gan -RealGate
```
