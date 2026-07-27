### Other changes

- Capped `fastapi` below 0.140. FastAPI 0.140.0 made `Dependant` a slotted dataclass, and FastStream's FastAPI plugin monkey-patches `model`, `custom_fields`, and `flat_params` onto that instance, which now raises `AttributeError: 'Dependant' object has no attribute 'model' and no __dict__ for setting new attributes`. The upstream bug (ag2ai/faststream#2959) is still open and unfixed on FastStream 0.7.2, so the cap keeps the next dependency refresh from baking a broken FastAPI into the lockfile. Remove the cap once FastStream is fixed.
