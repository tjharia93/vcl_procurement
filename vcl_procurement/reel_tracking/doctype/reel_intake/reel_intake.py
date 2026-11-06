from frappe.model.document import Document


class ReelIntake(Document):
    """Permanent tag reservation and arrival evidence, not a stock balance.

    Item may be unknown when labels are printed. A later Batch uses this same
    identity; quantity and valuation remain exclusively in stock transactions.
    Changes go through the role- and source-checked reel_intake API.
    """
    pass
