import json
from loader import (
    load_all,
    get_class, get_subclass, get_background,
    get_creature, get_race, get_item, get_ability,
    list_classes, list_subclasses, list_backgrounds,
    list_sapient_creatures, list_races, list_items, list_abilities,
)

biggus_dict = load_all("./Realms/dnd/")
print(json.dumps(biggus_dict, indent=4))
