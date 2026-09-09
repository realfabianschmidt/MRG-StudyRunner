"""Participant field vocabulary shared by identity handling and its card."""
PARTICIPANT_FIELD_ORDER = [
    "first_name",
    "last_name",
    "age_group",
    "gender",
    "childhood_area",
    "childhood_nearest_city",
    "birth_place",
    "birth_date",
]

PARTICIPANT_FIELD_DEFAULTS = {
    "first_name": {"enabled": True, "use_for_key": True, "store": False, "required": True},
    "last_name": {"enabled": True, "use_for_key": True, "store": False, "required": True},
    "age_group": {"enabled": True, "use_for_key": True, "store": True, "required": True},
    "gender": {"enabled": False, "use_for_key": False, "store": True, "required": True},
    "childhood_area": {"enabled": True, "use_for_key": True, "store": True, "required": True},
    "childhood_nearest_city": {"enabled": True, "use_for_key": True, "store": True, "required": True},
    "birth_place": {"enabled": False, "use_for_key": False, "store": True, "required": True},
    "birth_date": {"enabled": False, "use_for_key": False, "store": True, "required": True},
}

AGE_GROUP_DEFAULT_OPTIONS = ["18-25", "26-35", "36-45", "46-60", "60+"]

GENDER_DEFAULT_OPTIONS = ["Female", "Male", "Non-binary", "Prefer not to say"]

CONFIGURABLE_OPTION_DEFAULTS = {
    "age_group": AGE_GROUP_DEFAULT_OPTIONS,
    "gender": GENDER_DEFAULT_OPTIONS,
}

CHILDHOOD_AREA_OPTIONS = {"urban", "rural"}
