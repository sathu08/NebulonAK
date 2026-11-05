"""nak.harness — execution layer (engine + tools).

Worker stays individual (identity + one Brain call). THIS package owns
doing: the engine runs tool calls suggested by Brain/harness/policy.
All LLM still via nak.brain.Brain -> NebulonMD.
"""
