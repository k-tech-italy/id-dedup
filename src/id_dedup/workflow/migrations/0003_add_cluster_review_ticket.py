import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflow', '0002_add_batch_conversation'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClusterReviewTicket',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('cluster_label', models.IntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('batch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tickets',
                    to='workflow.batch',
                )),
            ],
        ),
        migrations.AddField(
            model_name='image',
            name='ticket',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='images',
                to='workflow.clusterreviewticket',
            ),
        ),
    ]
