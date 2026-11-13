// Retired draft: the shipped plugin is GflowPlugin in index.mjs.
// This module intentionally has no exports so magpie's "every exported
// function is a plugin" loader cannot register it a second time.
const PROVIDER_ID = "gflow";
const DEFAULT_MODEL_ID = "nano-banana-2.1";
const NPM = "@ai-sdk/openai-compatible";
void [PROVIDER_ID, DEFAULT_MODEL_ID, NPM];
