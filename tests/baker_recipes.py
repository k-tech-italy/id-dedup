from django.utils import timezone
from model_bakery.recipe import Recipe

from id_dedup.dedup.models import Identity
from id_dedup.workflow.models import Batch, ClusterReviewTicket, Conversation, Trigger

batch = Recipe(Batch)
open_ticket = Recipe(ClusterReviewTicket, cluster_label=0)
closed_ticket = Recipe(ClusterReviewTicket, cluster_label=1, closed_at=timezone.now)
open_conversation = Recipe(Conversation, trigger=Trigger.UPLOAD)
completed_conversation = Recipe(Conversation, trigger=Trigger.UPLOAD, ended_at=timezone.now)
errored_conversation = Recipe(Conversation, trigger=Trigger.UPLOAD, error_message="oops")
identity = Recipe(Identity, centroid=None)
