"""What every implementation of a port must satisfy.

Each module here is a base class of tests written against the interface rather than against any
implementation. A new adapter proves itself by subclassing the suite and supplying a fixture;
it does not get to write its own version of these.

That is what makes the simulators used in tests and the real adapters used in production
comparable: both pass the same suite, so a divergence between them shows up here rather than
on somebody's phone call.
"""
