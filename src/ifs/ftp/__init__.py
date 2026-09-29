"""FTPS-Gateway (Protokolladapter) – kein direkter Dateisystemzugriff.

Alle Operationen laufen über den IFS-Kern (Rechte, Namespace, Blobs). Das
virtuelle Dateisystem bildet FTP-Pfade auf Namespace-Einträge ab; Inhalte
werden aus S3 gestreamt bzw. dorthin gespoolt.
"""

from __future__ import annotations
