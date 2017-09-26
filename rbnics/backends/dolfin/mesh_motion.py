# Copyright (C) 2015-2017 by the RBniCS authors
#
# This file is part of RBniCS.
#
# RBniCS is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# RBniCS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with RBniCS. If not, see <http://www.gnu.org/licenses/>.
#

from dolfin import ALE, cells, Function, FunctionSpace, log, MeshFunctionSizet, PROGRESS, VectorFunctionSpace
from rbnics.backends.abstract import MeshMotion as AbstractMeshMotion
from rbnics.backends.dolfin.wrapping import ParametrizedExpression, ufl_lagrange_interpolation
from rbnics.utils.decorators import BackendFor, tuple_of
from mpi4py.MPI import MAX, MIN

@BackendFor("dolfin", inputs=(FunctionSpace, MeshFunctionSizet, tuple_of(tuple_of(str))))
class MeshMotion(AbstractMeshMotion):
    def __init__(self, V, subdomains, shape_parametrization_expression):
        # Store dolfin data structure related to the geometrical parametrization
        self.mesh = subdomains.mesh()
        self.subdomains = subdomains
        self.reference_coordinates = self.mesh.coordinates().copy()
        self.deformation_V = VectorFunctionSpace(self.mesh, "Lagrange", 1)
        self.subdomain_id_to_deformation_dofs = dict() # from int to list
        for cell in cells(self.mesh):
            subdomain_id = int(self.subdomains[cell]) - 1 # tuple start from 0, while subdomains from 1
            if subdomain_id not in self.subdomain_id_to_deformation_dofs:
                self.subdomain_id_to_deformation_dofs[subdomain_id] = list()
            dofs = self.deformation_V.dofmap().cell_dofs(cell.index())
            for dof in dofs:
                global_dof = self.deformation_V.dofmap().local_to_global_index(dof)
                if (
                    self.deformation_V.dofmap().ownership_range()[0] <= global_dof
                        and
                    global_dof < self.deformation_V.dofmap().ownership_range()[1]
                ):
                    self.subdomain_id_to_deformation_dofs[subdomain_id].append(dof)
        # In parallel some subdomains may not be present on all processors. Fill in
        # the dict with empty lists if that is the case
        mpi_comm = self.mesh.mpi_comm().tompi4py()
        min_subdomain_id = mpi_comm.allreduce(min(self.subdomain_id_to_deformation_dofs.keys()), op=MIN)
        max_subdomain_id = mpi_comm.allreduce(max(self.subdomain_id_to_deformation_dofs.keys()), op=MAX)
        for subdomain_id in range(min_subdomain_id, max_subdomain_id + 1):
            if subdomain_id not in self.subdomain_id_to_deformation_dofs:
                self.subdomain_id_to_deformation_dofs[subdomain_id] = list()
        # Subdomain numbering is contiguous
        assert min(self.subdomain_id_to_deformation_dofs.keys()) == 0
        assert len(self.subdomain_id_to_deformation_dofs.keys()) == max(self.subdomain_id_to_deformation_dofs.keys()) + 1
        
        # Store the shape parametrization expression
        self.shape_parametrization_expression = shape_parametrization_expression
        assert len(self.shape_parametrization_expression) == len(self.subdomain_id_to_deformation_dofs.keys())
        
    def init(self, problem):
        # Preprocess the shape parametrization expression to convert it in the displacement expression
        # This cannot be done during __init__ because at construction time the number
        # of parameters is still unknown
        self.displacement_expression = list()
        for shape_parametrization_expression_on_subdomain in self.shape_parametrization_expression:
            displacement_expression_on_subdomain = list()
            assert len(shape_parametrization_expression_on_subdomain) == self.mesh.geometry().dim()
            for (component, shape_parametrization_component_on_subdomain) in enumerate(shape_parametrization_expression_on_subdomain):
                # convert from shape parametrization T to displacement d = T - I
                displacement_expression_on_subdomain.append(
                    shape_parametrization_component_on_subdomain + " - x[" + str(component) + "]",
                )
            self.displacement_expression.append(
                ParametrizedExpression(
                    problem,
                    tuple(displacement_expression_on_subdomain),
                    mu=problem.mu,
                    element=self.deformation_V.ufl_element(),
                    domain=self.mesh
                )
            )
        
    def move_mesh(self):
        log(PROGRESS, "moving mesh")
        displacement = self.compute_displacement()
        ALE.move(self.mesh, displacement)
        
    def reset_reference(self):
        log(PROGRESS, "back to the reference mesh")
        self.mesh.coordinates()[:] = self.reference_coordinates
        
    # Auxiliary method to deform the domain
    def compute_displacement(self):
        displacement = Function(self.deformation_V)
        for (subdomain, displacement_expression_on_subdomain) in enumerate(self.displacement_expression):
            displacement_function_on_subdomain = Function(self.deformation_V)
            ufl_lagrange_interpolation(displacement_function_on_subdomain, displacement_expression_on_subdomain)
            subdomain_dofs = self.subdomain_id_to_deformation_dofs[subdomain]
            displacement.vector()[subdomain_dofs] = displacement_function_on_subdomain.vector()[subdomain_dofs]
        return displacement
