"""Turn composed-resource models into what the Crossplane SDK will accept."""


def to_dict(model) -> dict:
    """Serialize a composed-resource model for resource.update().

    by_alias=True because resource.update() dumps a model without it, and a field whose CRD name
    is a Python keyword is stored under an alias in the generated models; without the alias the
    manifest carries a name the CRD doesn't declare. exclude_none emits exactly the fields that
    were set, plus non-null schema defaults such as a kubernetes Object's watch: false.
    """
    return model.model_dump(by_alias=True, exclude_none=True, warnings=False)
