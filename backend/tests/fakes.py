class FakeHolded:
    """Doble del cliente de Holded para documentos y plan contable."""

    def __init__(self, total=None, attach_error=None, accounts=None, draft=True):
        self.total, self.attach_error, self.draft = total, attach_error, draft
        # número de cuenta → nombre
        self.accounts = dict(
            accounts
            if accounts is not None
            else {70500000: "Prestaciones de servicios", 70500003: "Otros asesoramientos"}
        )
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.attached: list[tuple] = []
        self.created_accounts: list[tuple[int, str | None]] = []

    def accounting_accounts(self):
        return {number: f"acc-{number}" for number in self.accounts}

    def accounting_account_names(self):
        return dict(self.accounts)

    def create_accounting_account(self, prefix, name):
        # Como Holded: la siguiente libre del prefijo; sin nombre, el de la cuenta padre
        number = max(n for n in self.accounts if n // 10_000 == prefix) + 1
        self.accounts[number] = name or self.accounts[prefix * 10_000]
        self.created_accounts.append((number, name))
        return f"acc-{number}"

    def create_document(self, doc_type, payload):
        self.created.append((doc_type, payload))
        return "doc-1"

    def update_document(self, doc_type, document_id, payload):
        self.updated.append((doc_type, document_id, payload))

    def attach_document_file(self, doc_type, document_id, filename, content, content_type):
        if self.attach_error:
            raise self.attach_error
        self.attached.append((doc_type, document_id, filename, content, content_type))

    def get_document(self, doc_type, document_id):
        return {"id": document_id, "total": self.total, "draft": True if self.draft else None}
