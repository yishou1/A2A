import atexit
import json
import os
import socket
import threading
import time
from datetime import datetime, timezone

import nacos
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_env_file(path=os.path.join(PROJECT_ROOT, ".env")):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class NacosRegistry:
    def __init__(self, server_addresses=None, namespace=None):
        load_env_file()
        if server_addresses is None:
            server_addresses = os.environ.get("NACOS_ADDR", "127.0.0.1:8848")
        if namespace is None:
            namespace = os.environ.get("NACOS_NAMESPACE", "public")

        self.server_addresses = server_addresses
        self.namespace = namespace
        self.client = nacos.NacosClient(server_addresses, namespace=namespace)
        self.http = requests.Session()
        self.http.trust_env = False
        self.default_heartbeat_interval = float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5"))
        self.heartbeat_grace_seconds = float(
            os.environ.get(
                "A2A_HEARTBEAT_GRACE_SECONDS",
                str(max(12.0, self.default_heartbeat_interval * 2.0 + 2.0)),
            )
        )
        self._heartbeat_supervisors = {}
        self._heartbeat_lock = threading.Lock()
        atexit.register(self.close)

    def register_service(
        self,
        service_name,
        ip,
        port,
        metadata=None,
        heartbeat_interval=None,
        ephemeral=True,
        cluster_name=None,
        group_name="DEFAULT_GROUP",
    ):
        if metadata is None:
            metadata = {}
        metadata = dict(metadata)
        metadata.setdefault("heartbeat_ts", int(time.time()))
        metadata.setdefault("heartbeat_at", utc_now_iso())

        if heartbeat_interval is None:
            heartbeat_interval = self.default_heartbeat_interval

        try:
            self.client.add_naming_instance(
                service_name,
                ip,
                port,
                cluster_name=cluster_name,
                metadata=metadata,
                ephemeral=ephemeral,
                group_name=group_name,
                heartbeat_interval=None,
            )
            print(f"Registered {service_name} at {ip}:{port} via SDK")
        except Exception as sdk_error:
            print(f"Nacos SDK register failed for {service_name}: {sdk_error}. Trying HTTP fallback.")
            self._register_service_http(
                service_name,
                ip,
                port,
                metadata,
                ephemeral=ephemeral,
                cluster_name=cluster_name,
                group_name=group_name,
            )

        if ephemeral and heartbeat_interval and heartbeat_interval > 0:
            self._start_heartbeat(
                service_name=service_name,
                ip=ip,
                port=port,
                metadata=metadata,
                heartbeat_interval=float(heartbeat_interval),
                cluster_name=cluster_name,
                group_name=group_name,
                ephemeral=ephemeral,
            )

    def _register_service_sdk(self, service_name, ip, port, metadata, ephemeral=True, cluster_name=None, group_name="DEFAULT_GROUP"):
        try:
            self.client.add_naming_instance(
                service_name,
                ip,
                port,
                cluster_name=cluster_name,
                metadata=metadata,
                ephemeral=ephemeral,
                group_name=group_name,
                heartbeat_interval=None,
            )
            print(f"Registered {service_name} at {ip}:{port}")
        except Exception as e:
            print(f"Failed to register service {service_name}: {e}")
            raise

    def discover_service(self, service_name, required_tags=None):
        try:
            instances = self._discover_service_http(service_name)
            return self._filter_instances(instances, required_tags)
        except Exception as http_error:
            print(f"Nacos HTTP discovery failed for {service_name}: {http_error}. Trying SDK fallback.")
            try:
                instances = self.client.list_naming_instance(service_name)
                return self._filter_instances(instances, required_tags)
            except Exception as sdk_error:
                print(f"Discovery error: {sdk_error}")
                return []

    def _base_url(self):
        # Use the first configured Nacos address. The project passes a single address today.
        address = self.server_addresses.split(",")[0].strip()
        if not address.startswith(("http://", "https://")):
            address = f"http://{address}"
        return f"{address}/nacos/v1/ns"

    def _namespace_params(self):
        # Nacos' built-in public namespace uses an empty namespaceId in the HTTP API.
        if self.namespace and self.namespace != "public":
            return {"namespaceId": self.namespace}
        return {}

    def _heartbeat_key(self, service_name, ip, port):
        return f"{service_name}#{ip}#{port}"

    def _register_service_http(self, service_name, ip, port, metadata, ephemeral=True, cluster_name=None, group_name="DEFAULT_GROUP"):
        params = {
            "serviceName": service_name,
            "ip": ip,
            "port": port,
            "metadata": json.dumps(metadata, separators=(",", ":")),
            "ephemeral": "true" if ephemeral else "false",
            **self._namespace_params(),
        }
        if cluster_name is not None:
            params["clusterName"] = cluster_name
        if group_name:
            params["groupName"] = group_name
        response = self.http.post(f"{self._base_url()}/instance", params=params, timeout=5)
        response.raise_for_status()
        print(f"Registered {service_name} at {ip}:{port} via HTTP fallback")

    def update_instance_metadata(
        self,
        service_name,
        instance,
        metadata_updates=None,
        remove_keys=None,
    ):
        ip = instance.get("ip")
        port = instance.get("port")
        cluster_name = instance.get("clusterName") or instance.get("cluster_name")
        group_name = instance.get("groupName") or instance.get("group_name") or "DEFAULT_GROUP"
        ephemeral = instance.get("ephemeral", True)
        metadata = dict(instance.get("metadata", {}) or {})
        metadata.update(metadata_updates or {})
        for key in remove_keys or []:
            metadata.pop(key, None)
        metadata["heartbeat_ts"] = int(time.time())
        metadata["heartbeat_at"] = utc_now_iso()

        try:
            self.client.modify_naming_instance(
                service_name,
                ip,
                port,
                cluster_name=cluster_name,
                metadata=metadata,
                ephemeral=ephemeral,
                group_name=group_name,
            )
        except Exception as sdk_error:
            print(
                f"Nacos SDK metadata update failed for {service_name} at "
                f"{ip}:{port}: {sdk_error}. Trying HTTP fallback."
            )
            self._update_instance_metadata_http(
                service_name,
                ip,
                port,
                metadata,
                ephemeral=ephemeral,
                cluster_name=cluster_name,
                group_name=group_name,
            )

        instance["metadata"] = metadata
        self._update_heartbeat_metadata(service_name, ip, port, metadata)
        return metadata

    def _update_instance_metadata_http(
        self,
        service_name,
        ip,
        port,
        metadata,
        ephemeral=True,
        cluster_name=None,
        group_name="DEFAULT_GROUP",
    ):
        params = {
            "serviceName": service_name,
            "ip": ip,
            "port": port,
            "metadata": json.dumps(metadata, separators=(",", ":")),
            "ephemeral": "true" if ephemeral else "false",
            "groupName": group_name,
            **self._namespace_params(),
        }
        if cluster_name is not None:
            params["clusterName"] = cluster_name
        response = self.http.put(f"{self._base_url()}/instance", params=params, timeout=5)
        response.raise_for_status()

    def _update_heartbeat_metadata(self, service_name, ip, port, metadata):
        heartbeat_key = self._heartbeat_key(service_name, ip, port)
        with self._heartbeat_lock:
            supervisor = self._heartbeat_supervisors.get(heartbeat_key)
        if supervisor:
            supervisor.update_metadata(metadata)

    def _send_heartbeat_http(self, service_name, ip, port, cluster_name=None, weight=1.0, metadata=None, ephemeral=True, group_name="DEFAULT_GROUP"):
        beat_data = {
            "serviceName": service_name,
            "ip": ip,
            "port": port,
            "weight": weight,
            "ephemeral": ephemeral,
        }
        if cluster_name is not None:
            beat_data["cluster"] = cluster_name
        if metadata is not None:
            beat_data["metadata"] = metadata

        params = {
            "serviceName": service_name,
            "beat": json.dumps(beat_data),
            "groupName": group_name,
            **self._namespace_params(),
        }
        response = self.http.put(f"{self._base_url()}/instance/beat", params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    def send_heartbeat(self, service_name, ip, port, cluster_name=None, weight=1.0, metadata=None, ephemeral=True, group_name="DEFAULT_GROUP"):
        heartbeat_metadata = dict(metadata or {})
        heartbeat_metadata["heartbeat_ts"] = int(time.time())
        heartbeat_metadata["heartbeat_at"] = utc_now_iso()

        try:
            return self.client.send_heartbeat(
                service_name,
                ip,
                port,
                cluster_name=cluster_name,
                weight=weight,
                metadata=heartbeat_metadata,
                ephemeral=ephemeral,
                group_name=group_name,
            )
        except Exception as client_error:
            print(f"Nacos SDK heartbeat failed for {service_name}: {client_error}. Trying HTTP fallback.")
            return self._send_heartbeat_http(
                service_name,
                ip,
                port,
                cluster_name=cluster_name,
                weight=weight,
                metadata=heartbeat_metadata,
                ephemeral=ephemeral,
                group_name=group_name,
            )

    def _start_heartbeat(self, service_name, ip, port, metadata, heartbeat_interval, cluster_name=None, group_name="DEFAULT_GROUP", ephemeral=True):
        supervisor = AgentHeartbeatSupervisor(
            registry=self,
            service_name=service_name,
            ip=ip,
            port=port,
            metadata=metadata,
            heartbeat_interval=heartbeat_interval,
            cluster_name=cluster_name,
            group_name=group_name,
            ephemeral=ephemeral,
        )

        heartbeat_key = self._heartbeat_key(service_name, ip, port)
        with self._heartbeat_lock:
            existing = self._heartbeat_supervisors.get(heartbeat_key)
            if existing:
                existing.stop()
            self._heartbeat_supervisors[heartbeat_key] = supervisor

        supervisor.start()
        return supervisor

    def close(self):
        with self._heartbeat_lock:
            supervisors = list(self._heartbeat_supervisors.values())
            self._heartbeat_supervisors.clear()

        for supervisor in supervisors:
            supervisor.stop()

    @staticmethod
    def _parse_heartbeat_ts(metadata):
        if not metadata:
            return None

        heartbeat_ts = metadata.get("heartbeat_ts") or metadata.get("last_heartbeat")
        if heartbeat_ts is None:
            return None

        try:
            return float(heartbeat_ts)
        except (TypeError, ValueError):
            try:
                normalized = str(heartbeat_ts).replace("Z", "+00:00")
                return datetime.fromisoformat(normalized).timestamp()
            except Exception:
                return None

    def _is_instance_fresh(self, instance):
        metadata = instance.get("metadata", {}) or {}
        heartbeat_ts = self._parse_heartbeat_ts(metadata)
        if heartbeat_ts is None:
            return False
        return (time.time() - heartbeat_ts) <= self.heartbeat_grace_seconds

    def _discover_service_http(self, service_name):
        params = {
            "serviceName": service_name,
            **self._namespace_params(),
        }
        response = self.http.get(f"{self._base_url()}/instance/list", params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    def _filter_instances(self, instances, required_tags=None):
        enabled_instances = [i for i in instances.get("hosts", []) if i.get("enabled", True)]
        healthy_instances = [i for i in enabled_instances if i.get("healthy")]
        candidate_instances = healthy_instances or enabled_instances
        candidate_instances = [i for i in candidate_instances if self._is_instance_fresh(i)]

        if not required_tags:
            return candidate_instances

        # Filter by tags
        matched = []
        for inst in candidate_instances:
            meta = inst.get("metadata", {})
            match = True
            for k, v in required_tags.items():
                if meta.get(k) != v:
                    match = False
                    break
            if match:
                matched.append(inst)
        return matched

def get_host_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


class AgentHeartbeatSupervisor(threading.Thread):
    def __init__(
        self,
        registry: NacosRegistry,
        service_name: str,
        ip: str,
        port: int,
        metadata=None,
        heartbeat_interval: float = 5.0,
        cluster_name=None,
        group_name: str = "DEFAULT_GROUP",
        ephemeral: bool = True,
    ):
        super().__init__(daemon=True)
        self.registry = registry
        self.service_name = service_name
        self.ip = ip
        self.port = port
        self.metadata = dict(metadata or {})
        self.heartbeat_interval = float(heartbeat_interval)
        self.cluster_name = cluster_name
        self.group_name = group_name
        self.ephemeral = ephemeral
        self._stop_event = threading.Event()
        self._metadata_lock = threading.Lock()

    def update_metadata(self, metadata):
        with self._metadata_lock:
            self.metadata = dict(metadata or {})

    def run(self):
        heartbeat_key = f"{self.service_name}#{self.ip}#{self.port}"
        print(
            f"[HEARTBEAT] started for {heartbeat_key} every {self.heartbeat_interval:.1f}s"
        )
        while not self._stop_event.is_set():
            with self._metadata_lock:
                heartbeat_metadata = dict(self.metadata)
            heartbeat_metadata["heartbeat_ts"] = int(time.time())
            heartbeat_metadata["heartbeat_at"] = utc_now_iso()

            try:
                self.registry.send_heartbeat(
                    self.service_name,
                    self.ip,
                    self.port,
                    cluster_name=self.cluster_name,
                    metadata=heartbeat_metadata,
                    ephemeral=self.ephemeral,
                    group_name=self.group_name,
                )
            except Exception as exc:
                print(f"[HEARTBEAT] failed for {heartbeat_key}: {exc}")

            if self._stop_event.wait(self.heartbeat_interval):
                break

        print(f"[HEARTBEAT] stopped for {heartbeat_key}")

    def stop(self):
        self._stop_event.set()
