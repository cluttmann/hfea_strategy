# HFEA Strategy with Alpaca and Google Cloud Functions

This project contains a set of Python Cloud Functions for managing a High-Frequency Enhanced Allocation (HFEA) strategy using Alpaca's trading API.

## Project Structure

- `main.py`: The main Python script containing the logic for monthly buys and rebalancing.
- `requirements.txt`: Python dependencies for the project.
- `cloudbuild.yaml`: Google Cloud Build configuration for deploying the Cloud Functions.
- `.gitignore`: Specifies files and directories to be ignored by Git.
- `README.md`: Overview and setup instructions for the project.

## Setup

### Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [Python 3.7+](https://www.python.org/downloads/)

### Installing Dependencies

First, clone the repository and navigate into the project directory:

```bash
git clone https://github.com/yourusername/hfea-alpaca-strategy.git
cd hfea-alpaca-strategy
