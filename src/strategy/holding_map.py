from dataclasses import dataclass


@dataclass
class HoldingTarget:
    multiplier: float       # Alvo de valorização
    sell_percent: float     # % da posição original a ser vendida


class HoldingMap:
    def __init__(self):
        """
        Escada Assimétrica Otimizada para Caudas Longas (v0.7).
        Reduz a liquidação em 2.0x para manter 65% do estoque surfando
        multiplicadores expressivos (5x, 15x e 50x Moonbag).
        """
        self.targets = [
            HoldingTarget(multiplier=2.0, sell_percent=0.35),   # 1º Alvo: Recupera 70% do capital inicial e mantém 65% no ativo
            HoldingTarget(multiplier=5.0, sell_percent=0.30),   # 2º Alvo: Trava lucro pesado (+150% do capital inicial)
            HoldingTarget(multiplier=15.0, sell_percent=0.20),  # 3º Alvo: Expansão parabólica (+300% do capital inicial)
            HoldingTarget(multiplier=50.0, sell_percent=0.15),  # 4º Alvo: Moonbag assimétrica extrema (+750%)
        ]

    def evaluate_exit(self, entry_price: float, current_price: float) -> list[str]:
        if entry_price <= 0:
            return []

        ratio = current_price / entry_price
        actions = []
        for target in self.targets:
            if ratio >= target.multiplier:
                actions.append(f"Gatilho {target.multiplier:.1f}x atingido: Vender {target.sell_percent * 100:.0f}%")
        return actions
