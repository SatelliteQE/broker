class ActionRegistry:
    CALLABLES = {}

    def __init__(self, cli_name):
        self.cli_name = cli_name
        self.func = None

    def __call__(self, func):
        self.func = func
        return self

    def __set_name__(self, owner, name):
        self.CALLABLES[self.cli_name] = owner, self.func
        # then replace ourself with the original method
        setattr(owner, name, self.func)
