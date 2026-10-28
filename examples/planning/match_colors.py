# Handwritten offline fixture, not model output.
for obj in get_obj_names():
    if is_block(obj):
        put_first_on_second(obj, get_color(obj) + ' bowl')
