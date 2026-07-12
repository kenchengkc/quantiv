# Quantiv system architecture

This document describes Quantiv's production data flow, application services, live quote system, and optional ML backend. For installation and common contributor commands, start with the root [README](../README.md).

## System overview

Quantiv is organized around three independent paths:

1. **Static dashboard generation** — scheduled data synchronization, validation, feature generation, scoring, and JSON publication