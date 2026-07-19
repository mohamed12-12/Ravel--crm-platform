# Tool Manager

## Purpose

Provide a production-facing wrapper around the tool registry so tools can be added without changing Gemini-specific code.

## Design

`ToolManager` owns a registry of `ToolSpec` objects and exposes higher-level descriptions for orchestration and future policy checks.

## Flow

1. Tool specs are loaded from the registry.
2. The manager exposes readable descriptions and metadata.
3. The planner uses the manager to reason over available capabilities.
4. The Gemini runtime still receives the Gemini-compatible declarations.

## Interfaces

- `ToolManager.describe()`
- `ToolManager.get(name)`
- `ManagedTool.from_spec(...)`

## Future Extensions

- permission enforcement
- tool policy groups
- dynamic tool discovery
- read/write tool separation

