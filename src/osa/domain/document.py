"""Closed vocabularies used to describe documents without inferring ownership."""

from __future__ import annotations

from enum import StrEnum


class Department(StrEnum):
    FISCAL = "Fiscal"
    ACCOUNTING = "Contábil"
    PERSONNEL = "Departamento Pessoal"
    CORPORATE = "Societário"


class DocumentType(StrEnum):
    CONTRACT_SOCIAL = "contract_social"
    CONTRACT_AMENDMENT = "contract_amendment"
    TAX_DOCUMENT = "tax_document"
    ACCOUNTING_DOCUMENT = "accounting_document"
    PAYROLL_DOCUMENT = "payroll_document"
    OTHER = "other"
