from dolfin import *
from ufl.geometry import *
import numpy as np
from ufl import Jacobian
from sys import argv
from dolfin.cpp.mesh import *
from mshr import *
from matplotlib import pyplot
import ufl
from wurlitzer import pipes
from numpy import random

parameters["linear_algebra_backend"] = "PETSc"
args = "--petsc.snes_linesearch_monitor --petsc.snes_linesearch_type bt"
parameters.parse(argv = argv[0:1] + args.split())


def sigmaVisc(u,mu):
    """
    The viscous part of the Cauchy stress, in terms of velocity ``u`` and
    dynamic viscosity ``mu``.
    """
    return 2.0*mu*sym(grad(u))

def sigma(u,p,mu):
    """
    The fluid Cauchy stress, in terms of velocity ``u``, pressure ``p``, 
    and dynamic viscosity ``mu``.
    """
    return sigmaVisc(u,mu) - p*Identity(ufl.shape(u)[0])

def materialTimeDerivative(u,u_t=None,f=None):
    """
    The fluid material time derivative, in terms of the velocity ``u``, 
    the partial time derivative ``u_t`` (which may be omitted for steady
    problems), and body force per unit mass, ``f``.
    """
    DuDt = dot(u,nabla_grad(u))
    if(u_t != None):
        DuDt += u_t
    if(f != None):
        DuDt -= f
    return DuDt
def meshMetric(mesh):
    """
    Extract mesh size tensor from a given ``mesh``.
    This returns the physical element metric tensor, ``G`` as a 
    UFL object.
    """
    dx_dxiHat = 0.5*ufl.Jacobian(mesh)
    dxiHat_dx = inv(dx_dxiHat)
    G = dxiHat_dx.T*dxiHat_dx
    return G

def stableNeumannBC(traction,u,v,n,g=None,ds=ds,gamma=Constant(1.0)):
    """
    This function returns the boundary contribution of a stable Neumann BC
    corresponding to a boundary ``traction`` when the velocity ``u`` (with 
    corresponding test function ``v``) is flowing out of the domain, 
    as determined by comparison with the outward-pointing normal, ``n``.  
    The optional velocity ``g`` can be used to offset the boundary velocity,
    as when this term is used to obtain a(n inflow-
    stabilized) consistent traction for weak enforcement of Dirichlet BCs.  
    The paramter ``gamma`` can optionally be used to scale the
    inflow term.  The BC is integrated using the optionally-specified 
    boundary measure ``ds``, which defaults to the entire boundary.
    NOTE: The sign convention here assumes that the return value is 
    ADDED to the residual given by ``interiorResidual``.
    NOTE: The boundary traction enforced differs from ``traction`` if 
    ``gamma`` is nonzero.  A pure traction BC is not generally stable,
    which is why the default ``gamma`` is one.  See
    https://www.oden.utexas.edu/media/reports/2004/0431.pdf
    for theory in the advection--diffusion model problem, and 
    https://doi.org/10.1007/s00466-011-0599-0
    for discussion in the context of Navier--Stokes.  
    """
    if(g==None):
        u_minus_g = u
    else:
        u_minus_g = u-g
    return -(inner(traction,v)
             + gamma*ufl.Min(inner(u,n),Constant(0.0))
             *inner(u_minus_g,v))*ds

def weakDirichletBC(u,p,v,q,g,nu,mesh,ds=ds,G=None,
                    sym=True,C_pen=Constant(1e3),
                    overPenalize=False):
    """
    This returns the variational form corresponding to a weakly-enforced 
    velocity Dirichlet BC, with data ``g``, on the boundary measure
    given by ``ds``, defaulting to the full boundary of the domain given by
    ``mesh``.  It takes as parameters an unknown velocity, ``u``, 
    unknown pressure ``p``, corresponding test functions ``v`` and ``q``, 
    mass density ``rho``, and viscosity ``mu``.  Optionally, the 
    non-symmetric variant can be used by overriding ``sym``.  ``C_pen`` is
    a dimensionless scaling factor on the penalty term.  The penalty term
    is omitted if ``not sym``, unless ``overPenalize`` is 
    optionally set to ``True``.  The argument ``G`` can optionally be given 
    a non-``None`` value, to use an alternate mesh size tensor.  If left 
    as ``None``, it will be set to the output of ``meshMetric(mesh)``.
    NOTE: The sign convention here assumes that the return value is 
    ADDED to the residual given by ``interiorResidual``.
    For additional information on the theory, see
    https://doi.org/10.1016/j.compfluid.2005.07.012
    """
    n = FacetNormal(mesh)
    sgn = 1.0
    if(not sym):
        sgn = -1.0
    if G == None:
        G = meshMetric(mesh) # $\sim h^{-2}$
    traction = sigma(u,p,nu)*n
    consistencyTerm = stableNeumannBC(traction,u,v,n,g=g,ds=ds)
    # Note sign of ``q``, negative for stability, regardless of ``sym``.    
    adjointConsistency = -sgn*dot(sigma(v,-sgn*q,nu)*n,u-g)*ds
    # Only term we need to change
    hb = 2*sqrt(dot(n,G*n))
    penalty = C_pen*nu*hb*dot((u-g),v)*ds
    retval = consistencyTerm + adjointConsistency
    if(overPenalize or sym):
        retval += penalty
        print("Passing here")
    return retval

def strongResidual(u,p,nu,u_t=None,f=None):
    """
    The momentum and continuity residuals, as a tuple, of the strong PDE,
    system, in terms of velocity ``u``, pressure ``p``, dynamic viscosity
    ``mu``, mass density ``rho``, and, optionally, the partial time derivative
    of velocity, ``u_t``, and a body force per unit mass, ``f``.  
    """
    DuDt = materialTimeDerivative(u,u_t,f)
    i,j = ufl.indices(2)
    r_M = DuDt - as_tensor(grad(sigma(u,p,nu))[i,j,j],(i,))
    r_C = div(u)
    return r_M, r_C

class PeriodicBoundary(SubDomain):

    def inside(self, x, on_boundary):
        # return True if on left or bottom boundary AND NOT on one of the two slave edges
        return bool((near(x[0], 0) or near(x[1], 0)) and 
            (not ((near(x[0], delta_x) and near(x[1], 0)) or 
                  (near(x[0], 0) and near(x[1], delta_y)))) and on_boundary)

    def map(self, x, y):
        if near(x[0], delta_x) and near(x[1], delta_y):
            y[0] = x[0] - delta_x
            y[1] = x[1] - delta_y 
            y[2] = x[2] 
        elif near(x[0], delta_x):
            y[0] = x[0] - delta_x
            y[1] = x[1]
            y[2] = x[2]
        elif near(x[1], delta_y):
            y[0] = x[0]
            y[1] = x[1] - delta_y
            y[2] = x[2]
        else:
            y[0] = -1000
            y[1] = -1000
            y[2] = -1000
delta_x = 2*pi
delta_y = 2
delta_z = 2/3*pi
rx = 5
ry = 5
rz = 5
delta_pressure = Constant(1.0)
nu = Constant(1.47e-4)
f = Constant((3.37e-3,0.,0.))
q_degree = 3
dx = dx(metadata={'quadrature_degree': q_degree})
 
# Create mesh
mesh = BoxMesh(Point(0,0,0),Point(delta_x,delta_y,delta_z),int(rx*delta_x),int(ry*delta_y),int(rz*delta_z))

# Create subdomains
subdomains = MeshFunction("size_t", mesh, 2)
subdomains.set_all(0)

# Create boundaries
class Walls(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and \
            (abs(x[2]) < DOLFIN_EPS or abs(x[2] - delta_z) < DOLFIN_EPS)
        
class Outlet(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and abs(x[0] - delta_x) < DOLFIN_EPS
        
class Inlet(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and abs(x[0]) < DOLFIN_EPS

class Sides(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and \
            (abs(x[1]) < DOLFIN_EPS or abs(x[1] - delta_y) < DOLFIN_EPS)

class AllBoundary(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary

boundaries = MeshFunction("size_t", mesh, mesh.topology().dim()-1, 0)
boundaries.set_all(0)
walls_ID = 1
walls = Walls()
walls.mark(boundaries, walls_ID)
outlet_ID = 2
outlet = Outlet()
outlet.mark(boundaries, outlet_ID)
inlet_ID = 3
inlet = Inlet()
inlet.mark(boundaries, inlet_ID)
sides_ID = 4
sides = Sides()
sides.mark(boundaries, sides_ID)

ds_bc = Measure('ds', domain=mesh, subdomain_data=boundaries, subdomain_id=1, metadata = {'quadrature_degree': 2})

# Save to xml file
File("Cylinder.xml") << mesh
File("Cylinder_physical_region.xml") << subdomains
File("Cylinder_facet_region.xml") << boundaries

# Save to pvd file for visualization
xdmf = XDMFFile(mesh.mpi_comm(), "Cylinder_mesh.xdmf")
xdmf.write(mesh)
xdmf = XDMFFile(mesh.mpi_comm(), "Cylinder_physical_region.xdmf")
subdomains.rename("subdomains", "subdomains")
xdmf.write(subdomains)
xdmf = XDMFFile(mesh.mpi_comm(), "Cylinder_facet_region.xdmf")
boundaries.rename("boundaries", "boundaries")
xdmf.write(boundaries)


Re = 120.
u_bar = 1.
u_in = Expression(("delta_pressure/2/nu*x[2]*(delta_z-x[2])", "0.", "0."), t=0, delta_z = delta_z, u_bar=u_bar, nu=nu, delta_pressure=delta_pressure, degree=2) 
p_in = Expression('0', degree=1)
# nu = Constant(u_bar*0.1/Re) # obtained from the definition of Re = u_bar * diam / nu. In our case diam = 0.1.

dt = 0.002
T = 200 * dt # should be 15 to generate the video

"""### Function spaces"""
pbc = PeriodicBoundary()
V_element = VectorElement("Lagrange", mesh.ufl_cell(), 1)
Q_element = FiniteElement("Lagrange", mesh.ufl_cell(), 1)
W_element = MixedElement(V_element, Q_element) # Taylor-Hood
W = FunctionSpace(mesh, W_element, constrained_domain=PeriodicBoundary())

"""### Test and trial functions (for the increment)"""
vq                 = TestFunction(W) # Test function in the mixed space
delta_up           = TrialFunction(W) # Trial function in the mixed space (XXX Note: for the increment!)
(delta_u, delta_p) = split(delta_up) # Function in each subspace to write the functional  (XXX Note: for the increment!)
(v, q)             = split(      vq) # Test function in each subspace to write the functional

"""### <font color="red">Solution (which will be obtained starting from the increment at the current time)</font>"""
up = Function(W)
(u, p) = split(up)

"""### <font color="red">Solution at the previous time</font>"""
up_prev = Function(W)
(u_prev, _) = split(up_prev)
up_bc = Function(W)
(u_bc, _) = split(up_bc)

## Here some tests I was doing to convert fenics objects to numpy
u_0 = interpolate(u_in, W.sub(0).collapse())
u_bc = interpolate(u_in, W.sub(0).collapse())
p_0 = interpolate(p_in, W.sub(1).collapse())
u_0.vector().set_local(u_0.vector().get_local()+10*(0.5-random.random(u_0.vector().size())))
#u_bc = u_0.copy()
u_bc.vector().set_local(np.ones(u_bc.vector().size()))
assign(up_prev , [u_0,p_0])
assign(up , [u_0,p_0])
u_t = (u - u_prev)/dt
# Preparation of Variational forms.
#K = JacobianInverse(mesh)
#G = K.T*K
G = meshMetric(mesh)
#gg = (K[0,0] + K[1,0] + K[2,0])**2 + (K[0,1] + K[1,1] + K[2,1])**2 + (K[0,2] + K[1,2] + K[2,2])**2
#rm = (u-u_prev)/dt + grad(p) + grad(u)*u - nu*(u.dx(0).dx(0) + u.dx(1).dx(1) + u.dx(2).dx(2)) - f
DuDt = materialTimeDerivative(u,u_t,f)
i,j = ufl.indices(2)
rm = DuDt - as_tensor(grad(sigma(u,p,nu))[i,j,j],(i,))
rc = div(u)
C_I = Constant(36.0)
C_t = Constant(4.0)
denom2 = inner(u,G*u) + C_I*nu*nu*inner(G,G) + DOLFIN_EPS
if(dt != None):
    denom2 += C_t/dt**2
tm = 1/sqrt(denom2)
#tm=(4*((dt)**(-2)) + 36*(nu**2)*inner(G,G) + inner(u,G*u))**(-0.5)
tc=1.0/(tm*tr(G))#!/usr/bin/env python
tcross = outer((tm*rm),(tm*rm))

uPrime = -tm*rm
pPrime = -tc*rc

F = (inner(DuDt,v) + inner(sigma(u,p,nu),grad(v))
    + inner(div(u),q)
    - inner(dot(u,nabla_grad(v)) + grad(q), uPrime)
    - inner(pPrime,div(v))
    + inner(v,dot(uPrime,nabla_grad(u)))
    - inner(grad(v),outer(uPrime,uPrime))- inner(f,v))*dx

F += weakDirichletBC(u,p,v,q,u_bc,nu,mesh,ds_bc)

J = derivative(F, up, delta_up)



"""### Boundary conditions (for the solution)"""

walls_bc       = DirichletBC(W.sub(0), Constant((0., 0., 0.)), boundaries, walls_ID )
sides_bc       = DirichletBC(W.sub(0).sub(1), Constant(0.), boundaries, sides_ID )
inlet_bc       = DirichletBC(W.sub(1), Constant(1.),       boundaries, inlet_ID )
outlet_bc       = DirichletBC(W.sub(1), Constant(0.),      boundaries, outlet_ID )

bc = [inlet_bc, outlet_bc, sides_bc]

snes_solver_parameters = {"nonlinear_solver": "snes",
                          "snes_solver": {"linear_solver": "mumps",
                                          "maximum_iterations": 20,
                                          "report": True,
                                          "error_on_nonconvergence": True}}
problem = NonlinearVariationalProblem(F, up, bc, J)
solver  = NonlinearVariationalSolver(problem)
solver.parameters.update(snes_solver_parameters)

outfile_u = File("out_6/u.pvd")
outfile_p = File("out_6/p.pvd")

(u, p) = up.split()
outfile_u << u
outfile_p << p

K = int(T/dt)
for i in range(1, K):
    # Compute the current time
    t = i*dt
    print("t =", t)
    # Update the time for the boundary condition
    u_in.t = t
    # Solve the nonlinear problem
    # with pipes() as (out, err):
    #solver.solve()
    solve(F == 0, up, bcs=bc)
    # Store the solution in up_prev
    assign(up_prev, up)
    # Plot
    (u, p) = up.split()
    outfile_u << u
    outfile_p << p