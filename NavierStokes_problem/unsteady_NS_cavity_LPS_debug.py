from dolfin import *
from ufl.geometry import *
from dolfin.cpp.mesh import *
from mshr import *
from problems import Problem
import matplotlib.pyplot as plt
import numpy as np

# Print log messages only from the root process in parallel
parameters["std_out_all_processes"] = False;

linear_implicit = True

with_plot = True

# Create mesh
Nx=64
problem_name = "cylinder"#"lid-driven_cavity"#"lid-driven_cavity"
physical_problem = Problem(problem_name, Nx)
mesh = physical_problem.mesh

# Set parameter values
nu_value = 0.001
u_top_value = 1.
reynolds_value = physical_problem.get_reynolds(u_top_value, nu_value)
print("Reynolds number = ",reynolds_value)
nu = Constant(nu_value)
Re= Constant(reynolds_value )
f = Constant((0., 0.))
u_top = Constant(u_top_value)



# XXX Time discretization
CFL = 0.5
T = 5.
dtplot =0.5
Nt_max = 10000
dT=Constant(1.)

degree_poly=2
scalar_element = FiniteElement("CG", mesh.ufl_cell(), degree_poly)
vector_element = VectorElement("CG", mesh.ufl_cell(), degree_poly)
system_element = MixedElement( vector_element , scalar_element )
dg0_element = FiniteElement("DG", mesh.ufl_cell(),0)
V0 = FunctionSpace(mesh, dg0_element)
W = FunctionSpace(mesh,system_element)


# Define trial and test functions

if linear_implicit:
    up = TrialFunction(W)
    (u, p) = split(up)
else:
    up = Function(W)
    (u, p) = split(up)


up_prev = Function(W)
(u_prev, p_prev) = split(up_prev)


up_diff = Function(W)

vq = TestFunction(W)
(v, q)  = split(vq)

up_sol = Function(W)
(u_sol, p_sol) = split(up_sol)

bcs = physical_problem.define_bc(W, u_top)


# Define the forms
h = function.specialfunctions.CellDiameter(mesh)
hmin = mesh.hmin()

nu_local = nu

def sigma_star(v):
    return v


c1 = Constant(1.)
c2 = Constant(1.)
nu_local = nu


def define_form(up, up_prev,vq):
    (u,p)           = split(up)
    (u_prev,p_prev) = split(up_prev)
    (v,q)           = split(vq)

    tau_den = c1*(nu)/(h/degree_poly)**2+c2*project(sqrt(u_prev[0]**2+u_prev[1]**2),V0)/(h/degree_poly)
    tau_v   = project(1./(1./dT+tau_den),V0)
    tau_p   = tau_v
    tau_d = project((h/degree_poly)**2/c1*tau_den,V0)

    
    b_form_lin = 0.5*(inner(dot(u_prev,nabla_grad(u)),v)  - inner(dot(u_prev,nabla_grad(v)),u)) *dx
    b_form_nl =  0.5*(inner(dot(u,nabla_grad(u)),v)  - inner(dot(u,nabla_grad(v)),u)) *dx
    # b_form_lin = inner(dot(u_prev,nabla_grad(u)),v)*dx
    # b_form_nl =  inner(dot(u,nabla_grad(u)),v) *dx  

    s_conv_lin = 0.5*tau_v*inner(sigma_star(dot(u_prev,nabla_grad(u_prev))),sigma_star(dot(u_prev,nabla_grad(v))))*dx  
    
    s_conv_nl = 0.5*tau_v*inner(sigma_star(dot(u_prev,nabla_grad(u_prev))),sigma_star(dot(u_prev,nabla_grad(v))))*dx
                   
  
    
    if linear_implicit:
        b_form = b_form_lin
        s_conv = s_conv_lin
    else:
        b_form = b_form_nl
        s_conv = s_conv_nl

    a_form = nu*inner(sym(grad(u)),sym(grad(v)))*dx
    #a_form = nu*inner(grad(u),grad(v))*dx
       
    s_div  = (tau_d*inner(sigma_star(div(u)),sigma_star(div(v)))) *dx
    s_pres = (tau_p*inner(sigma_star(grad(p)),sigma_star(grad(q)))) *dx

    
    F = inner((u-u_prev)/dT,v)*dx + b_form + a_form - inner(p,div(v))*dx\
        + inner(div(u),q)*dx + s_pres +s_conv + s_div

    return F



# Export the initial solution (zero)
outfile_u = File(physical_problem.name+"_unsteady_new/u.pvd")
outfile_p = File(physical_problem.name+"_unsteady_new/p.pvd")
outfile_ld = File(physical_problem.name+"_unsteady_new/ld.pvd")

(u_sol,p_sol) = up_sol.split()
outfile_u << u_sol
outfile_p << p_sol

sav_ts = float(5) 

# XXX Time loop

time=0.
it=0
tplot=0.
while time <T and it < Nt_max:
    u2 = project(u_sol[0]**2+u_sol[1]**2,V0)
    if u2.vector().max()<1e-8:
        dt=CFL*hmin
    else:
        dt = CFL*project(h/u2,V0).vector().min()
    dT.assign(Constant(dt))
    # Compute the current time
    time = time+ dt
    it+=1
    print("t =", time, "dt = ", dt)
    
    F = define_form(up, up_prev,vq)
    if linear_implicit:
        ll = lhs(F)
        rr = rhs(F)
    else:
        J = derivative(F, up)
        problem = NonlinearVariationalProblem(F, up, bcs, J)
        solver  = NonlinearVariationalSolver(problem)
    # Solve the nonlinear problem
    if linear_implicit:
        LL = assemble(ll)
        RR = assemble(rr)
        [bc.apply(LL,RR) for bc in bcs]
        solve(LL,up_sol.vector(),RR)
    else:
        solver.solve()
        up_sol.assign(up)
    # Store the solution in up_prev
    up_diff.assign(up_sol - up_prev)
    (u_diff, p_diff) = up_diff.split()
    u_diff_norm = np.sqrt(assemble(inner(u_diff,u_diff)*dx))/dt
    p_diff_norm = np.sqrt(assemble(inner(p_diff,p_diff)*dx))/dt
    F_lim =define_form(up_sol,up_prev,vq)
    res = norm(assemble(F_lim))

    assign(up_prev, up_sol)

    print(f"Residual {res}, time derivative u {u_diff_norm}, time derivative p {p_diff_norm}")
    # Plot
    (u_sol, p_sol) = up_sol.split()
    tplot+= dt
    if tplot > dtplot:
        tplot = tplot - dtplot
        outfile_u << u_sol
        outfile_p << p_sol
    
outfile_u << u_sol
outfile_p << p_sol

# solver.solve()
# # Plot
# (u, p) = up.split()
# outfile_u << u
# outfile_p << p

plt.figure()
pp=plot(p_sol); plt.colorbar(pp)
plt.title("Pressure")
plt.show(block=False)

plt.figure()
pp=plot(u_sol[0]); plt.colorbar(pp)
plt.title("u")
plt.show(block=False)

plt.figure()
pp=plot(u_sol[1]); plt.colorbar(pp)
plt.title("v")
plt.show(block=False)


plt.figure()
pp=plot(u_sol); plt.colorbar(pp)
plt.title("Velocity")
plt.show()
