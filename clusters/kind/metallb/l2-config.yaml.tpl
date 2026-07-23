apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: {{POOL_NAME}}
  namespace: {{NAMESPACE}}
spec:
  addresses:
    - {{ADDRESS_RANGE}}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: {{POOL_NAME}}-l2
  namespace: {{NAMESPACE}}
spec:
  ipAddressPools:
    - {{POOL_NAME}}
