"""The agent roster.

Each agent is a system prompt (in prompts/), a tool budget, and an output schema.
They share one grounded tool layer and one citation namespace, but never share a
message history during research -- that isolation is what makes the debate real.
"""
