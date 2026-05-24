PUBLIC_CHANNEL_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
PUBLIC_CHANNEL_NAME = "Public"


def is_public_channel_key(key: str) -> bool:
    return key.upper() == PUBLIC_CHANNEL_KEY


def is_public_channel_name(name: str) -> bool:
    return name.casefold() == PUBLIC_CHANNEL_NAME.casefold()
