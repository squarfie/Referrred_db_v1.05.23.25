"""Laboratory model interface; schema and migration ownership remain in receiving.

These are aliases of existing classes, so app labels and database tables stay stable.
"""

from apps.receiving.models import (
    ReferralReceipt,
    ReceivingEvent,
    LaboratoryReceiving,
    LaboratoryAccession,
    LaboratoryOrganismCount,
    LaboratoryIsolate,
    LaboratoryIdentification,
    LaboratoryBiobank,
)

__all__ = ['ReferralReceipt', 'ReceivingEvent', 'LaboratoryReceiving', 'LaboratoryAccession', 'LaboratoryOrganismCount', 'LaboratoryIsolate', 'LaboratoryIdentification', 'LaboratoryBiobank']
