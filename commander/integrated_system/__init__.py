__all__ = ["build_integrated_demo_app", "IntegratedDemoOrchestrator"]


def build_integrated_demo_app(*args, **kwargs):
    from integrated_system.app import build_integrated_demo_app as _build_integrated_demo_app

    return _build_integrated_demo_app(*args, **kwargs)


def IntegratedDemoOrchestrator(*args, **kwargs):
    from integrated_system.orchestrator import IntegratedDemoOrchestrator as _IntegratedDemoOrchestrator

    return _IntegratedDemoOrchestrator(*args, **kwargs)
