#!/usr/bin/env python3
"""
pcb_auto_placement/ga_optimizer.py

Genetic Algorithm optimizer using DEAP framework.
Refines force-directed placement to minimize wirelength and eliminate overlaps.

Features:
  - Elitism: best individual always preserved
  - Adaptive mutation: sigma decreases over generations
  - Overlap repair after mutation
  - Per-component (x,y) paired mutation
  - Mixed initialization (80% perturbed, 20% random exploration)
  - Congestion cost in fitness evaluation
  - Edge packing penalty
"""

import random
import numpy as np
from multiprocessing import Pool
from deap import base, creator, tools, algorithms

from cost_functions import compute_total_cost
from geometry_utils import extract_pose, rotated_size


def placement_to_individual(placement: dict, refs: list) -> list:
    """Convert placement dict to a flat list of [x1, y1, x2, y2, ...]."""
    individual = []
    for ref in refs:
        x, y, _ = extract_pose(placement.get(ref, (0, 0)))
        individual.extend([x, y])
    return individual


def individual_to_placement(individual: list, refs: list) -> dict:
    """Convert flat list back to placement dict."""
    # Improvement #4: cleaner zip-based conversion
    return {ref: (individual[i * 2], individual[i * 2 + 1])
            for i, ref in enumerate(refs)}


def optimize(initial_placement: dict, G, sizes: dict,
             board_w: float, board_h: float,
             holes: list = None, keepouts: list = None,
             population_size: int = 80, generations: int = 150,
             mutation_sigma: float = 2.0, seed: int = 42,
             outline_clearance: float = None,
             component_clearance: float = None,
             micro_mode: bool = False,
             frozen_components: set = None,
             rotations: dict = None,
             edge_keepout_margin: float = 0.0,
             sides: dict = None,
             tht_refs: set = None,
             block_pairs: list = None,
             block_separation_distance: float = 5.0,
             relative_constraints: list = None,
             footprint_metadata: dict = None,
             constraint_fixed_placement: dict = None) -> dict:
    """
    Use DEAP Genetic Algorithm to optimize component placement.

    Args:
        initial_placement: dict {ref: (x, y)} from force-directed layout
        G: NetworkX connectivity graph
        sizes: dict {ref: (w, h)} component sizes
        board_w, board_h: board dimensions in mm
        holes: list of mounting hole dicts
        keepouts: list of keepout zone dicts
        population_size: GA population size
        generations: number of GA generations
        mutation_sigma: initial std dev for Gaussian mutation (mm)
        seed: random seed
        outline_clearance: board edge clearance from DRC (mm)
        component_clearance: component-to-component clearance from DRC (mm)
        block_pairs: LLM separate-hint (refs_a, refs_b) tuples fed to the
            block-separation penalty; None keeps the term off
        block_separation_distance: minimum centroid distance per pair (mm)

    Returns:
        dict {ref: (x, y)} optimized placement
    """
    random.seed(seed)
    np.random.seed(seed)

    # Bug fix #3: guard against None clearances
    outline_clearance = outline_clearance or 0
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    component_clearance = component_clearance or 0

    holes = holes or []
    keepouts = keepouts or []
    frozen_components = frozen_components or set()
    rotations = rotations or {}
    tht_refs = tht_refs or set()
    refs = sorted(initial_placement.keys())
    n_vars = len(refs) * 2  # x, y for each component
    if not refs:
        return {}

    oriented_sizes = {
        ref: rotated_size(sizes.get(ref, (3.0, 3.0)), rotation=rotations.get(ref, 0.0))
        for ref in refs
    }

    # --- DEAP Setup ---
    if hasattr(creator, "FitnessMin"):
        del creator.FitnessMin
    if hasattr(creator, "Individual"):
        del creator.Individual

    creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
    creator.create("Individual", list, fitness=creator.FitnessMin)

    toolbox = base.Toolbox()

    base_individual = placement_to_individual(initial_placement, refs)

    def clamp_individual(ind):
        """Enforce strict board boundary constraints using DRC outline_clearance."""
        for i in range(len(refs)):
            w, h = oriented_sizes.get(refs[i], (3.0, 3.0))
            min_x = w / 2 + effective_outline
            max_x = board_w - w / 2 - effective_outline
            min_y = h / 2 + effective_outline
            max_y = board_h - h / 2 - effective_outline
            ind[i * 2] = max(min_x, min(max_x, ind[i * 2]))
            ind[i * 2 + 1] = max(min_y, min(max_y, ind[i * 2 + 1]))
        return ind

    # Improvement #3: Soft overlap repair after mutation
    def resolve_overlaps(ind):
        """Push apart overlapping components with a few iterations.

        Side-aware mode: two SMD parts on opposite sides may legally overlap,
        so they are left alone (pushing them apart would undo bottom-side
        board-area relief).
        """
        for _ in range(3):  # Quick repair passes
            for i in range(len(refs)):
                for j in range(i + 1, len(refs)):
                    x1, y1 = ind[i * 2], ind[i * 2 + 1]
                    x2, y2 = ind[j * 2], ind[j * 2 + 1]
                    w1, h1 = oriented_sizes.get(refs[i], (3.0, 3.0))
                    w2, h2 = oriented_sizes.get(refs[j], (3.0, 3.0))

                    if sides is not None and refs[i] not in tht_refs and refs[j] not in tht_refs:
                        if sides.get(refs[i], "top") != sides.get(refs[j], "top"):
                            continue

                    min_dx = (w1 + w2) / 2 + component_clearance
                    min_dy = (h1 + h2) / 2 + component_clearance

                    dx = abs(x1 - x2)
                    dy = abs(y1 - y2)

                    if dx < min_dx and dy < min_dy:
                        # Push apart along the axis with least overlap
                        overlap_x = min_dx - dx
                        overlap_y = min_dy - dy
                        if overlap_x < overlap_y:
                            shift = overlap_x / 2 + 0.1
                            if x1 < x2:
                                if refs[i] not in frozen_components: ind[i * 2] -= shift
                                if refs[j] not in frozen_components: ind[j * 2] += shift
                            else:
                                if refs[i] not in frozen_components: ind[i * 2] += shift
                                if refs[j] not in frozen_components: ind[j * 2] -= shift
                        else:
                            shift = overlap_y / 2 + 0.1
                            if y1 < y2:
                                if refs[i] not in frozen_components: ind[i * 2 + 1] -= shift
                                if refs[j] not in frozen_components: ind[j * 2 + 1] += shift
                            else:
                                if refs[i] not in frozen_components: ind[i * 2 + 1] += shift
                                if refs[j] not in frozen_components: ind[j * 2 + 1] -= shift
        clamp_individual(ind)
        return ind

    # Improvement #5: Mixed initialization (80% perturbed, 20% random)
    def create_individual():
        if not micro_mode and random.random() < 0.2:
            # 20% random exploration across the board
            ind = []
            for i in range(len(refs)):
                w, h = oriented_sizes.get(refs[i], (3.0, 3.0))
                x = random.uniform(w / 2 + effective_outline, board_w - w / 2 - effective_outline)
                y = random.uniform(h / 2 + effective_outline, board_h - h / 2 - effective_outline)
                ind.extend([x, y])
        else:
            # 80% perturbed from initial placement
            ind = list(base_individual)
            for i in range(len(refs)):
                if refs[i] not in frozen_components:
                    ind[i * 2] += random.gauss(0, mutation_sigma * 0.5)
                    ind[i * 2 + 1] += random.gauss(0, mutation_sigma * 0.5)
        clamp_individual(ind)
        return creator.Individual(ind)

    toolbox.register("individual", create_individual)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)

    # Fitness evaluation
    def evaluate(individual):
        placement = individual_to_placement(individual, refs)
        placement_with_rotation = {
            ref: (placement[ref][0], placement[ref][1], rotations.get(ref, 0.0))
            for ref in refs
        }
        cost = compute_total_cost(placement_with_rotation, G, sizes, board_w, board_h, holes, keepouts,
                                  outline_clearance=outline_clearance,
                                  component_clearance=component_clearance,
                                  edge_keepout_margin=edge_keepout_margin,
                                  sides=sides, tht_refs=tht_refs,
                                  block_pairs=block_pairs,
                                  block_separation_distance=block_separation_distance,
                                  relative_constraints=relative_constraints,
                                  footprint_metadata=footprint_metadata,
                                  constraint_fixed_placement=constraint_fixed_placement)
        return (cost,)

    toolbox.register("evaluate", evaluate)

    # Crossover: blend crossover for continuous values
    toolbox.register("mate", tools.cxBlend, alpha=0.3)

    # Improvement #2 & #4: Adaptive per-component mutation
    def mutate_placement(individual, gen=0):
        # Improvement #2: Adaptive sigma — large early, small late
        sigma = mutation_sigma * (1.0 - gen / max(generations, 1))
        sigma = max(sigma, 0.1)  # Floor to prevent zero mutation

        # Improvement #4: Mutate per-component (x,y together)
        for i in range(len(refs)):
            if refs[i] not in frozen_components and random.random() < 0.15:  # 15% chance per component
                dx = random.gauss(0, sigma)
                dy = random.gauss(0, sigma)
                individual[i * 2] += dx
                individual[i * 2 + 1] += dy

        clamp_individual(individual)

        # Improvement #3: Soft repair after mutation
        resolve_overlaps(individual)

        return (individual,)

    toolbox.register("mutate", mutate_placement)

    # Selection: tournament
    toolbox.register("select", tools.selTournament, tournsize=3)

    # --- Run GA ---
    pop = toolbox.population(n=population_size)

    # Add the original placement as one individual (elitism seed)
    elite = creator.Individual(list(base_individual))
    pop[0] = elite

    # Evaluate initial population
    fitnesses = list(map(toolbox.evaluate, pop))
    for ind, fit in zip(pop, fitnesses):
        ind.fitness.values = fit

    best_cost = min(f[0] for f in fitnesses)
    print(f"  [GA] Generation 0: Best cost = {best_cost:.1f}")
    
    loss_history = [best_cost]

    # Evolution loop
    for gen in range(1, generations + 1):
        # Select offspring
        offspring = toolbox.select(pop, len(pop) - 1)  # Leave room for elite
        offspring = list(map(toolbox.clone, offspring))

        # Apply crossover
        for child1, child2 in zip(offspring[::2], offspring[1::2]):
            if random.random() < 0.7:
                toolbox.mate(child1, child2)
                # Clamp after crossover (cxBlend can push values out of bounds)
                clamp_individual(child1)
                clamp_individual(child2)
                del child1.fitness.values
                del child2.fitness.values

        # Apply mutation with adaptive sigma
        for mutant in offspring:
            if random.random() < 0.3:
                mutate_placement(mutant, gen=gen)
                del mutant.fitness.values

        # Evaluate individuals with invalid fitness
        invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
        fitnesses = list(map(toolbox.evaluate, invalid_ind))
        for ind, fit in zip(invalid_ind, fitnesses):
            ind.fitness.values = fit

        # Improvement #1: Elitism — preserve the best individual
        current_elite = tools.selBest(pop, 1)[0]
        offspring_elite = tools.selBest(offspring, 1)[0]

        # Keep the overall best
        if current_elite.fitness.values[0] <= offspring_elite.fitness.values[0]:
            best_ever = toolbox.clone(current_elite)
        else:
            best_ever = toolbox.clone(offspring_elite)

        # Replace population: offspring + best individual
        pop[:] = [best_ever] + offspring

        # Track best
        current_best = min(ind.fitness.values[0] for ind in pop)
        loss_history.append(current_best)
        if gen % 20 == 0 or gen == generations:
            print(f"  [GA] Generation {gen}: Best cost = {current_best:.1f}")

    # Extract best individual
    best_ind = tools.selBest(pop, 1)[0]
    best_placement = individual_to_placement(best_ind, refs)

    # Round coordinates and enforce final strict clamp using DRC outline_clearance
    final_placement = {}
    for ref, (x, y) in best_placement.items():
        w, h = oriented_sizes.get(ref, (3.0, 3.0))
        x = round(max(w / 2 + outline_clearance, min(board_w - w / 2 - outline_clearance, x)), 2)
        y = round(max(h / 2 + outline_clearance, min(board_h - h / 2 - outline_clearance, y)), 2)
        final_placement[ref] = (x, y)
    best_placement = final_placement

    final_cost = best_ind.fitness.values[0]
    print(f"  [GA] Optimization complete. Final cost = {final_cost:.1f}")
    
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(10,6))
        plt.plot(range(len(loss_history)), loss_history, marker='o', color='red', linewidth=2, markersize=4)
        plt.title('GA Optimization Baseline (Current Engine)')
        plt.xlabel('Generation (Iteration)')
        plt.ylabel('Penalty Cost (Lower is Better)')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.tight_layout()
        
        import os
        # Save to output directory if possible, else root
        out_dir = "output" if os.path.exists("output") else "."
        plt.savefig(os.path.join(out_dir, "baseline_loss.png"))
        print(f"  [GA] Baseline loss plot saved to {os.path.join(out_dir, 'baseline_loss.png')}")
        plt.close()
    except Exception as e:
        print(f"  [GA] Could not plot baseline loss (is matplotlib installed?): {e}")

    return best_placement
