# Autonomous IT Helpdesk Swarm (Local AI + Active Directory)

A fully self-hosted, zero-subscription, multi-agent IT Helpdesk automation system. It ingests support tickets via email, routes them through specialized local LLM agents over **SwarmChat**, executes secure **Active Directory** operations via WinRM, and sends automated resolution emails back to the user.

## Architecture & Load Balancing

To avoid model-swapping bottlenecks on local hardware, LLM workloads are distributed across two separate Ollama hosts:

```mermaid
graph LR
    A[User Email - Gmail] -->|IMAP| B[Helpdesk Bot Engine]
    B --> C[SwarmChat Hub - Proxmox LXC]
    C --> D[Triage Agent - Host A: Qwen/Llama]
    D -->|Access/AD Ticket| E[Access Agent - Host B: Hermes 3]
    D -->|Hardware Ticket| F[Hardware Agent - Host A: Qwen/Llama]
    E -->|Whitelisted WinRM Action| G[Windows Server DC - Active Directory]
    G --> E
    E --> D
    F --> D
    D -->|SMTP Resolution| A
    
