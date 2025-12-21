# Chronomaps Server Documentation

This directory contains comprehensive documentation for the Chronomaps Server API.

## Contents

- [API.md](API.md) - Complete API reference documentation

## Overview

Chronomaps Server is a Firebase Cloud Functions-based backend for managing collaborative workspaces that collect and process future scenario screenshots. The system uses AI-powered analysis (GPT-4.1 Vision) to extract structured information from screenshots and provides ML-based clustering and visualization capabilities.

## Quick Start

1. **Authentication**: Obtain workspace keys (admin, collaborate, or view)
2. **Upload Screenshots**: Use the screenshot handler endpoint to analyze images
3. **Manage Items**: Create, read, update, and delete items via the REST API
4. **Cluster & Visualize**: Generate interactive maps of related scenarios

## Key Features

- Multi-tenant workspace architecture
- Fine-grained access control (5-level privilege system)
- AI-powered screenshot analysis using GPT-4.1 Vision
- Interactive chat agent for guided item completion
- ML clustering (t-SNE + Agglomerative Clustering)
- Map tile generation for visualization
- Email integration
- Real-time streaming for long-running operations

## Technology Stack

- Python 3.12 + Flask
- Firebase (Functions, Firestore, Storage, Auth)
- OpenAI GPT-4.1
- scikit-learn for ML clustering

## Documentation Structure

### API Reference

See [API.md](API.md) for complete endpoint documentation including:

- Authentication & Authorization
- Workspace Management
- Item Management
- AI-Powered Endpoints
- Data Models
- Error Responses
- Examples

## Support

For questions or issues, please refer to the main project repository.
