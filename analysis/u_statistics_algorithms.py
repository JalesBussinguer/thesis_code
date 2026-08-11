"""
Implementação dos Algoritmos 1, 2 e 3 do artigo:
"Hypothesis Testing, Separability, and Classification of Polarimetric SAR 
Intensity Data With Nonparametric U-Statistics"

Baseado no código IDL original de Negri et al.
"""

import os
import numpy as np
from concurrent.futures import ProcessPoolExecutor
from typing import List, Tuple, Optional, Union
from dataclasses import dataclass
from tqdm import tqdm  # Para progress bars (opcional)


_BOOTSTRAP_X = None
_BOOTSTRAP_GROUPS = None
_BOOTSTRAP_GROUP_INDEXES = None
_BOOTSTRAP_GAMMA = None


@dataclass
class StructBlocks:
    """Estrutura equivalente ao structBlocks do IDL"""
    N: np.ndarray  # Número de observações por grupo/amostra
    ptrBlocksImg: List[List[np.ndarray]]  # Lista de blocos (observações)


def kernel_dif(X: np.ndarray, Y: np.ndarray, gamma: float) -> float:
    """
    Kernel baseado na diferença (Phi1)
    Corresponde à norma L1 elevada à potência gamma
    """
    return np.mean(np.abs(X - Y) ** gamma)


def kernel_I(X: np.ndarray, Y: np.ndarray, threshold: float) -> float:
    """
    Kernel binário (Phi2)
    Retorna 1 se a diferença máxima exceder o limiar
    """
    return 1.0 if np.max(np.abs(X - Y)) > threshold else 0.0


def kernel_I__(X: np.ndarray, Y: np.ndarray, gamma: float) -> float:
    """
    Kernel de diferença máxima (Phi2 variante)
    """
    return float(np.max(np.abs(X - Y)))


def kernel(X: np.ndarray, Y: np.ndarray, kernel_type: int, kernel_par: float) -> float:
    """
    Função kernel wrapper
    
    Parameters:
    -----------
    X, Y : np.ndarray
        Vetores de observações (3 intensidades: HH, HV, VV)
    kernel_type : int
        1: kernel_dif (norma L1^gamma)
        2: kernel_I (binário com limiar)
        3: kernel_I__ (diferença máxima)
    kernel_par : float
        Parâmetro do kernel (gamma ou limiar)
    """
    if kernel_type == 1:
        return kernel_dif(X, Y, kernel_par)
    elif kernel_type == 2:
        return kernel_I(X, Y, kernel_par)
    elif kernel_type == 3:
        return kernel_I__(X, Y, kernel_par)
    else:
        raise ValueError(f"kernel_type desconhecido: {kernel_type}")


def _as_2d_array(X: List[np.ndarray] | np.ndarray) -> np.ndarray:
    if isinstance(X, np.ndarray):
        arr = X
    else:
        arr = np.asarray(X, dtype=float)

    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)

    return arr.astype(float, copy=False)


def _prepare_groups(group_indices: List[int]) -> Tuple[np.ndarray, np.ndarray, dict]:
    groups = np.asarray(group_indices)
    unique_groups = np.unique(groups)
    group_sizes = {g: int(np.sum(groups == g)) for g in unique_groups}
    return groups, unique_groups, group_sizes


def _compute_statistic_T_array(
    X: np.ndarray,
    group_indices: np.ndarray,
    gamma: float,
    block_size: int = 512,
) -> float:
    n_obs = X.shape[0]
    groups = np.unique(group_indices)
    group_sizes = {g: int(np.sum(group_indices == g)) for g in groups}
    N_total = n_obs

    same_group_coeff = {
        g: (-(N_total - group_size) / (group_size - 1) if group_size > 1 else 0.0)
        for g, group_size in group_sizes.items()
    }

    T = 0.0
    for start_i in range(0, n_obs, block_size):
        end_i = min(start_i + block_size, n_obs)
        X_i = X[start_i:end_i]
        g_i = group_indices[start_i:end_i]

        for start_j in range(0, n_obs, block_size):
            end_j = min(start_j + block_size, n_obs)
            X_j = X[start_j:end_j]
            g_j = group_indices[start_j:end_j]

            distances = np.sum(np.abs(X_i[:, None, :] - X_j[None, :, :]), axis=2) ** gamma
            eta = np.ones((end_i - start_i, end_j - start_j), dtype=float)

            same_mask = g_i[:, None] == g_j[None, :]
            if np.any(same_mask):
                for g, coeff in same_group_coeff.items():
                    if coeff == 0.0:
                        continue
                    eta[np.logical_and(same_mask, g_i[:, None] == g)] = coeff

            T += float(np.sum(eta * distances))

    return T


def _bootstrap_init(X: np.ndarray, groups: np.ndarray, group_indexes: List[np.ndarray], gamma: float) -> None:
    global _BOOTSTRAP_X, _BOOTSTRAP_GROUPS, _BOOTSTRAP_GROUP_INDEXES, _BOOTSTRAP_GAMMA
    _BOOTSTRAP_X = X
    _BOOTSTRAP_GROUPS = groups
    _BOOTSTRAP_GROUP_INDEXES = group_indexes
    _BOOTSTRAP_GAMMA = gamma


def _bootstrap_one(_: int) -> float:
    X_boot = []
    group_indices_boot = []

    for g, indices_g in zip(_BOOTSTRAP_GROUPS, _BOOTSTRAP_GROUP_INDEXES):
        boot_indices = np.random.choice(indices_g, size=len(indices_g), replace=True)
        X_boot.append(_BOOTSTRAP_X[boot_indices])
        group_indices_boot.append(np.full(len(boot_indices), g))

    X_boot_arr = np.vstack(X_boot)
    group_indices_boot_arr = np.concatenate(group_indices_boot)
    return _compute_statistic_T_array(X_boot_arr, group_indices_boot_arr, _BOOTSTRAP_GAMMA)


def compute_statistic_T(X: List[np.ndarray], 
                        group_indices: List[int], 
                        gamma: float) -> float:
    """
    Calcula a estatística T conforme equação (11) do artigo
    
    T(Z) = Σ η_ij * ||Z_i - Z_j||_1^γ
    
    Parameters:
    -----------
    X : List[np.ndarray]
        Lista de todas as observações
    group_indices : List[int]
        Índices dos grupos para cada observação
    gamma : float
        Parâmetro γ do kernel
    
    Returns:
    --------
    float: Valor da estatística T
    """
    X_arr = _as_2d_array(X)
    groups_arr, _, _ = _prepare_groups(group_indices)
    return _compute_statistic_T_array(X_arr, groups_arr, gamma)


def compute_statistic_T_within(X: List[np.ndarray], 
                               group_indices: List[int], 
                               gamma: float) -> float:
    """
    Calcula a estatística W (variabilidade intra-grupo)
    
    W = Σ_{i≠j} η_ij * ||X_i - X_j||_1^γ
    """
    n_obs = len(X)
    group_sizes = {}
    
    for idx, g in enumerate(group_indices):
        group_sizes[g] = group_sizes.get(g, 0) + 1
    
    W = 0.0
    N_total = len(X)
    
    for i in range(n_obs):
        for j in range(n_obs):
            if i == j:
                continue
                
            if group_indices[i] == group_indices[j]:
                group_size = group_sizes[group_indices[i]]
                eta = -(N_total - group_size) / (group_size - 1) if group_size > 1 else 0.0
                dist = np.linalg.norm(X[i] - X[j], ord=1) ** gamma
                W += eta * dist
    
    return W


def bootstrap_p_value(X: List[np.ndarray], 
                      group_indices: List[int], 
                      gamma: float, 
                      observed_T: float, 
                      B: int = 2000,
                      verbose: bool = False,
                      n_jobs: Optional[int] = None) -> float:
    """
    Calcula o p-valor via bootstrap
    
    Parameters:
    -----------
    X : List[np.ndarray]
        Lista de observações
    group_indices : List[int]
        Índices dos grupos
    gamma : float
        Parâmetro γ
    observed_T : float
        Estatística T observada
    B : int
        Número de reamostragens bootstrap
    verbose : bool
        Se True, mostra progresso
    
    Returns:
    --------
    float: p-valor
    """
    X_arr = _as_2d_array(X)
    groups_arr = np.asarray(group_indices)
    groups = np.unique(groups_arr)
    group_indexes = [np.where(groups_arr == g)[0] for g in groups]

    if n_jobs is None:
        n_jobs = max(1, (os.cpu_count() or 1) - 1)

    if n_jobs <= 1 or B < 2:
        iterator = range(B)
        if verbose:
            iterator = tqdm(iterator, desc="Bootstrap")

        T_bootstrap = []
        for _ in iterator:
            T_bootstrap.append(_bootstrap_one(0))
    else:
        iterator = range(B)
        if verbose:
            iterator = tqdm(iterator, desc="Bootstrap")

        with ProcessPoolExecutor(
            max_workers=n_jobs,
            initializer=_bootstrap_init,
            initargs=(X_arr, groups, group_indexes, gamma),
        ) as executor:
            T_bootstrap = list(executor.map(_bootstrap_one, iterator, chunksize=max(1, B // (n_jobs * 4))))

    T_bootstrap = np.asarray(T_bootstrap)
    return float(np.mean(T_bootstrap >= observed_T))


def homogeneity_test(X: List[np.ndarray], 
                group_indices: List[int], 
                gamma: float, 
                alpha: float = 0.05, 
                B: int = 2000,
                verbose: bool = False,
                n_jobs: Optional[int] = None) -> Tuple[float, bool]:
    """
    Algoritmo 1: Teste de homogeneidade baseado em U-statistics
    
    Parameters:
    -----------
    X : List[np.ndarray]
        Lista de observações (cada observação é um vetor de 3 intensidades)
    group_indices : List[int]
        Índices dos grupos para cada observação
    gamma : float
        Parâmetro γ da U-statistic
    alpha : float
        Nível de significância
    B : int
        Número de reamostragens bootstrap
    verbose : bool
        Se True, mostra progresso
    
    Returns:
    --------
    tuple: (p_value, reject_null)
        p_value: valor-p calculado
        reject_null: True se H0 é rejeitada (grupos são separáveis)
    """
    # Calcular estatística T observada
    observed_T = compute_statistic_T(X, group_indices, gamma)
    
    # Calcular p-valor via bootstrap
    p_value = bootstrap_p_value(X, group_indices, gamma, observed_T, B, verbose, n_jobs)
    
    # Decisão
    reject_null = p_value < alpha
    
    return p_value, reject_null


def hierarchical_separability_analysis(X: List[np.ndarray], 
                group_indices: List[int], 
                gamma: float, 
                alpha: float = 0.05, 
                B: int = 2000,
                verbose: bool = False) -> Tuple[List[int], dict]:
    """
    Algoritmo 2: Análise hierárquica de separabilidade
    
    Parameters:
    -----------
    X : List[np.ndarray]
        Lista de observações
    group_indices : List[int]
        Índices dos grupos atuais
    gamma : float
        Parâmetro γ
    alpha : float
        Nível de significância
    B : int
        Número de reamostragens bootstrap
    verbose : bool
        Se True, mostra progresso
    
    Returns:
    --------
    tuple: (final_group_indices, hierarchy_info)
        final_group_indices: índices finais após separação
        hierarchy_info: dicionário com informações hierárquicas
    """
    hierarchy = {
        'levels': [],
        'bipartitions': []
    }
    
    def recursive_split(X, group_ids, level=0):
        unique_groups = np.unique(group_ids)
        
        if len(unique_groups) <= 1:
            return group_ids
        
        # Testar homogeneidade atual
        p_val, reject = homogeneity_test(X, group_ids, gamma, alpha, B, verbose=False)
        
        hierarchy['levels'].append({
            'level': level,
            'n_groups': len(unique_groups),
            'p_value': p_val,
            'reject': reject
        })
        
        if not reject:
            # Mesclar todos os grupos
            return np.zeros(len(group_ids), dtype=int)
        
        # Tentar todas as bipartições possíveis
        best_partition = None
        best_ratio = np.inf
        
        # Gerar todas as bipartições dos grupos únicos
        from itertools import combinations
        
        n_unique = len(unique_groups)
        for k in range(1, n_unique // 2 + 1):
            for combo in combinations(unique_groups, k):
                # Criar nova atribuição: grupo A (combo) vs grupo B (restante)
                left_set = set(combo)
                
                # Atribuir novos rótulos: 0 para left, 1 para right
                new_ids = np.array([0 if g in left_set else 1 for g in group_ids])
                
                # Calcular T e W para esta bipartição
                T = compute_statistic_T(X, new_ids, gamma)
                W = compute_statistic_T_within(X, new_ids, gamma)
                ratio = W / T if T != 0 else np.inf
                
                if ratio < best_ratio:
                    best_ratio = ratio
                    best_partition = new_ids
                    best_left = list(combo)
        
        if best_partition is None:
            return group_ids
        
        # Registrar bipartição
        hierarchy['bipartitions'].append({
            'level': level,
            'left_groups': best_left,
            'right_groups': [g for g in unique_groups if g not in best_left],
            'ratio': best_ratio
        })
        
        # Recursivamente aplicar às duas partes
        # Reindexar grupos dentro de cada parte
        left_mask = best_partition == 0
        right_mask = best_partition == 1
        
        X_left = [X[i] for i in range(len(X)) if left_mask[i]]
        X_right = [X[i] for i in range(len(X)) if right_mask[i]]
        
        group_ids_left = best_partition[left_mask]
        group_ids_right = best_partition[right_mask]
        
        # Reindexar grupos para começar de 0 em cada parte
        unique_left = np.unique(group_ids_left)
        unique_right = np.unique(group_ids_right)
        
        map_left = {old: new for new, old in enumerate(unique_left)}
        map_right = {old: new + len(unique_left) for new, old in enumerate(unique_right)}
        
        new_ids_left = np.array([map_left[g] for g in group_ids_left])
        new_ids_right = np.array([map_right[g] for g in group_ids_right])
        
        # Recursão
        final_left = recursive_split(X_left, new_ids_left, level + 1)
        final_right = recursive_split(X_right, new_ids_right, level + 1)
        
        # Combinar resultados
        final_ids = np.zeros(len(group_ids), dtype=int)
        final_ids[left_mask] = final_left
        final_ids[right_mask] = final_right + (final_left.max() + 1 if len(final_left) > 0 else 0)
        
        return final_ids
    
    final_groups = recursive_split(X, np.array(group_indices))
    
    return final_groups, hierarchy


def image_classification(img: np.ndarray,
                training_data: List[np.ndarray], 
                training_labels: List[int],
                gamma: float, 
                rho: int,
                kernel_type: int = 1,
                verbose: bool = False) -> np.ndarray:
    """
    Algoritmo 3: Classificação de imagem baseada em U-statistics
    
    Parameters:
    -----------
    img : np.ndarray
        Imagem PolSAR com shape (height, width, 3) para intensidades HH, HV, VV
    training_data : List[np.ndarray]
        Dados de treinamento (vetores de 3 intensidades)
    training_labels : List[int]
        Rótulos dos dados de treinamento
    gamma : float
        Parâmetro γ da U-statistic
    rho : int
        Raio da vizinhança (ρ)
    kernel_type : int
        Tipo de kernel (1, 2 ou 3)
    verbose : bool
        Se True, mostra progresso
    
    Returns:
    --------
    np.ndarray: Mapa de classificação com shape (height, width)
    """
    height, width = img.shape[:2]
    n_classes = len(np.unique(training_labels))
    
    # Preparar dados de treinamento como lista de observações
    X_train = training_data
    y_train = training_labels
    
    # Mapa de classificação
    classification_map = np.zeros((height, width), dtype=int)
    
    # Para cada pixel na imagem
    iterator = range(height * width)
    if verbose:
        iterator = tqdm(iterator, desc="Classificando...")
    
    for idx in iterator:
        row = idx // width
        col = idx % width
        
        # Extrair vizinhança centrada no pixel
        r_min = max(0, row - rho)
        r_max = min(height, row + rho + 1)
        c_min = max(0, col - rho)
        c_max = min(width, col + rho + 1)
        
        # Coletar observações na vizinhança
        X_s = []
        for i in range(r_min, r_max):
            for j in range(c_min, c_max):
                X_s.append(img[i, j, :].copy())
        
        if len(X_s) == 0:
            continue
        
        # Avaliar cada classe possível
        T_values = []
        
        for class_label in range(n_classes):
            # Criar conjunto X_s,ℓ = (X_s, X_train) com X_s atribuído à classe ℓ
            all_observations = X_s + X_train
            
            # Índices dos grupos: X_s pertence a class_label, treinamento tem seus rótulos
            group_indices = ([class_label] * len(X_s) + 
                           list(y_train))
            
            # Calcular estatística T
            T = compute_statistic_T(all_observations, group_indices, gamma)
            T_values.append(T)
        
        # Atribuir à classe que maximiza T
        best_class = np.argmax(T_values)
        classification_map[row, col] = best_class
    
    return classification_map


# Wrappers com os nomes citados no artigo e no exemplo de uso.
def algorithm_1(X: List[np.ndarray],
                group_indices: List[int],
                gamma: float,
                alpha: float = 0.05,
                B: int = 2000,
                verbose: bool = False) -> Tuple[float, bool]:
    return homogeneity_test(X, group_indices, gamma, alpha, B, verbose)


def algorithm_2(X: List[np.ndarray],
                group_indices: List[int],
                gamma: float,
                alpha: float = 0.05,
                B: int = 2000,
                verbose: bool = False) -> Tuple[List[int], dict]:
    return hierarchical_separability_analysis(X, group_indices, gamma, alpha, B, verbose)


def algorithm_3(img: np.ndarray,
                training_data: List[np.ndarray],
                training_labels: List[int],
                gamma: float,
                rho: int,
                kernel_type: int = 1,
                verbose: bool = False) -> np.ndarray:
    return image_classification(img, training_data, training_labels, gamma, rho, kernel_type, verbose)


# Funções auxiliares para processamento de dados PolSAR
def extract_intensities(covariance_matrix: np.ndarray) -> np.ndarray:
    """
    Extrai as intensidades diagonais da matriz de covariância PolSAR
    
    Parameters:
    -----------
    covariance_matrix : np.ndarray
        Matriz de covariância 3x3 para cada pixel
    
    Returns:
    --------
    np.ndarray: Vetor [I_HH, I_HV, I_VV]
    """
    return np.array([covariance_matrix[0, 0].real,
                     covariance_matrix[1, 1].real,
                     covariance_matrix[2, 2].real])


def load_polsar_data(hh_image, hv_image, vv_image):
    """
    Carrega as três intensidades em um único array 3D
    
    Returns:
    --------
    np.ndarray: Array shape (height, width, 3) com intensidades
    """
    return np.stack([hh_image, hv_image, vv_image], axis=2)


# Exemplo de uso
if __name__ == "__main__":
    # Criar dados sintéticos para teste
    np.random.seed(42)
    
    # Simular 3 classes com diferentes distribuições
    class_1 = np.random.gamma(2, 0.5, (50, 3))  # Intensidades mais baixas
    class_2 = np.random.gamma(5, 0.5, (50, 3))  # Intensidades médias
    class_3 = np.random.gamma(10, 0.5, (50, 3)) # Intensidades mais altas
    
    # Dados de treinamento
    X_train = list(class_1[:30]) + list(class_2[:30]) + list(class_3[:30])
    y_train = [0]*30 + [1]*30 + [2]*30
    
    # Teste de homogeneidade (Algoritmo 1)
    print("=== Algoritmo 1: Teste de Homogeneidade ===")
    X_test = list(class_1[30:35]) + list(class_2[30:35]) + list(class_3[30:35])
    group_test = [0]*5 + [1]*5 + [2]*5
    
    p_val, reject = algorithm_1(X_test, group_test, gamma=1.0, alpha=0.05, B=100)
    print(f"p-valor: {p_val:.4f}")
    print(f"Rejeitar H0: {reject}")
    print()
    
    # Análise hierárquica (Algoritmo 2)
    print("=== Algoritmo 2: Análise Hierárquica ===")
    final_groups, hierarchy = algorithm_2(X_train, y_train, gamma=1.0, alpha=0.05, B=100)
    print(f"Grupos finais: {final_groups[:10]}...")
    print(f"Níveis hierárquicos: {len(hierarchy['levels'])}")
    print()
    
    # Classificação (Algoritmo 3) - exemplo com imagem pequena
    print("=== Algoritmo 3: Classificação ===")
    # Criar imagem sintética
    height, width = 50, 50
    img = np.zeros((height, width, 3))
    
    # Preencher com padrões
    img[:20, :20, :] = class_1[0]  # Classe 1 no canto superior esquerdo
    img[:20, 20:, :] = class_2[0]  # Classe 2 no canto superior direito
    img[20:, :20, :] = class_3[0]  # Classe 3 no canto inferior esquerdo
    img[20:, 20:, :] = class_1[0]  # Classe 1 no canto inferior direito
    
    # Adicionar ruído
    img += np.random.normal(0, 0.1, img.shape)
    
    # Classificar
    class_map = algorithm_3(img, X_train, y_train, gamma=1.0, rho=2, verbose=True)
    print(f"Mapa de classificação shape: {class_map.shape}")