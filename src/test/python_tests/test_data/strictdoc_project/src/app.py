"""
@relation(REQ-1, scope=file)
"""


def do_x():
    """
    @relation(REQ-1, scope=function)
    """
    return helper() + 1


def helper():
    return 41
