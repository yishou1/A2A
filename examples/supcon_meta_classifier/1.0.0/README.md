# SupCon Meta Classifier

M06 trained traditional neural-network implementation for the TIA small CPU
profile. It combines a two-layer projection MLP, supervised contrastive loss,
and learnable class prototypes.

Rebuild the deterministic reference dataset and checkpoint with:

```powershell
python scripts/train_supcon_meta_classifier.py
```

The recorded metrics use synthetic embedding clusters and do not represent
operational target-identification accuracy.
