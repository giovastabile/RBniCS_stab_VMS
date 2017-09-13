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

def basic_function_copy(backend, wrapping):
    def _basic_function_copy(function):
        original_vector = function.vector()
        v = backend.Vector(original_vector.N)
        v[:] = original_vector
        return backend.Function(v)
    return _basic_function_copy

# No explicit instantiation for backend = rbnics.backends.online.numpy to avoid
# circular dependencies. The concrete instatiation will be carried out in
# rbnics.backends.online.numpy.copy
