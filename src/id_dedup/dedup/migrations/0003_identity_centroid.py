# TODO (pre-release): squash or reset all migrations to a clean initial state
# before the first production release. The incremental history here is
# fine for beta development but should not ship as-is.

import numpy as np
import pgvector.django.indexes
import pgvector.django.vector
from django.db import migrations, models


def backfill_centroids(apps, schema_editor):
    Identity = apps.get_model("dedup", "Identity")
    Image = apps.get_model("dedup", "Image")

    for identity in Identity.objects.iterator():
        embeddings = list(Image.objects.filter(identity=identity).values_list("embedding", flat=True))
        if not embeddings:
            continue
        vecs = np.stack(embeddings)
        mean = vecs.mean(axis=0)
        identity.centroid = (mean / np.linalg.norm(mean)).tolist()
        identity.image_count = len(embeddings)
        identity.save(update_fields=["centroid", "image_count"])


class Migration(migrations.Migration):

    dependencies = [
        ('dedup', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='identity',
            name='centroid',
            field=pgvector.django.vector.VectorField(dimensions=512, null=True),
        ),
        migrations.AddField(
            model_name='identity',
            name='image_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name='identity',
            index=pgvector.django.indexes.HnswIndex(
                fields=['centroid'],
                name='identity_centroid_idx',
                opclasses=['vector_cosine_ops'],
            ),
        ),
        migrations.RunPython(backfill_centroids, reverse_code=migrations.RunPython.noop),
    ]
